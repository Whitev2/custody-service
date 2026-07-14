# синк canonical AssetModel из backend'а.
# Fireblocks asset ID НЕ храним - резолвим в рантайме по contract_address / blockchain.

from uuid import UUID

from sqlalchemy import select

from app.config import cfg, log
from app.models import AssetModel
from app.storage import get_db_local
from app.services.http_client import http_client
from app.services.custody.fireblocks.utils import (
    mapping_native_tokens,
    parse_fireblocks_asset,
)


def _get_testnet_name(is_testnet: bool, blockchain: str) -> str | None:
    # 'SEPOLIA'/'SHASTA'/... или None для mainnet
    if not is_testnet:
        return None

    testnet_map = {
        "ETHEREUM": "SEPOLIA",
        "TRON": "SHASTA", 
        "BITCOIN": "TESTNET",
        "BSC": "TESTNET",
        "POLYGON": "MUMBAI",
        "SOLANA": "DEVNET",
    }
    return testnet_map.get(blockchain.upper(), "TESTNET")


async def sync_fireblocks_assets():
    # 1) тянем контракты из backend, 2) создаём/обновляем assets, 3) токены линкуем к нативному через parent_id
    from app.services.custody.factory import get_provider

    try:
        is_testnet = cfg.app.is_testnet
        log.info(f"🌍 Environment: {cfg.app.STAND} (is_testnet={is_testnet})")

        backend_url = cfg.app.BACKEND_URL
        session = http_client.get_session()

        log.info(
            f"📡 Fetching contracts from backend: {backend_url}/internal/contracts?is_testnet={is_testnet}"
        )
        async with session.get(
            f"{backend_url}/internal/contracts",
            params={"is_testnet": str(is_testnet)},
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"Backend API error ({response.status}): {error_text}")
            contracts = await response.json()
        log.info(f"📦 Fetched {len(contracts)} contracts from backend")

        # ассеты из Fireblocks нужны для метаданных (decimals)
        provider = get_provider()
        fb_assets = await provider.get_supported_assets()
        log.info(f"📦 Fetched {len(fb_assets)} assets from Fireblocks")

        fb_by_address: dict[str, dict] = {}
        fb_by_id: dict[str, dict] = {}
        
        for fb_asset in fb_assets:
            asset_id = fb_asset.get("id", "")
            contract = fb_asset.get("contractAddress", "")
            issuer = fb_asset.get("issuerAddress", "")

            fb_by_id[asset_id] = fb_asset

            for addr in [contract, issuer]:
                if addr:
                    fb_by_address[addr.lower()] = fb_asset

        async with get_db_local() as db:
            created = 0
            updated = 0
            skipped = 0

            env_key = "dev" if is_testnet else "prod"
            env_mapping = mapping_native_tokens().get(env_key, {})

            native_contracts: list[dict] = []
            token_contracts: list[dict] = []

            for contract_info in contracts:
                contract_address = contract_info.get("contract_address")
                name = contract_info.get("name", "")

                fb_asset = fb_by_id.get(name)
                asset_type = fb_asset.get("type") if fb_asset else None
                
                if asset_type == "BASE_ASSET" or not contract_address:
                    native_contracts.append(contract_info)
                else:
                    token_contracts.append(contract_info)

            log.info(
                f"📊 Split contracts: {len(native_contracts)} native, {len(token_contracts)} tokens"
            )

            # blockchain -> asset_id
            native_cache: dict[str, UUID] = {}

            # phase 1: нативные монеты
            log.info("🔵 Phase 1: Loading native assets...")
            for contract_info in native_contracts:
                symbol = contract_info.get("name", "").upper()

                mapped_meta = env_mapping.get(symbol)
                if not mapped_meta:
                    log.warning(f"⚠️ Native asset {symbol} not in mapping, skipping")
                    skipped += 1
                    continue

                blockchain = mapped_meta["blockchain"]
                currency = mapped_meta["currency"]
                network = mapped_meta["network"]
                testnet_name = _get_testnet_name(is_testnet, blockchain)

                fb_asset_id = mapped_meta.get("asset_id", symbol)
                fb_asset = fb_by_id.get(fb_asset_id)
                decimals = fb_asset.get("decimals", 18) if fb_asset else 18

                stmt = select(AssetModel).where(
                    AssetModel.provider == "fireblocks",
                    AssetModel.blockchain == blockchain,
                    AssetModel.is_native.is_(True),
                )
                if testnet_name:
                    stmt = stmt.where(AssetModel.testnet == testnet_name)
                else:
                    stmt = stmt.where(AssetModel.testnet.is_(None))
                    
                result = await db.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.asset = fb_asset_id
                    existing.symbol = currency
                    existing.display_name = contract_info.get("display_name", currency)
                    existing.network = network
                    existing.decimals = decimals
                    existing.is_active = True
                    updated += 1
                    native_cache[blockchain] = existing.id
                    log.debug(f"🔄 Updated native: {currency} on {blockchain}")
                else:
                    native_asset = AssetModel(
                        asset=fb_asset_id,
                        provider="fireblocks",
                        symbol=currency,
                        display_name=contract_info.get("display_name", currency),
                        blockchain=blockchain,
                        network=network,
                        contract_address=None,
                        testnet=testnet_name,
                        decimals=decimals,
                        is_active=True,
                        is_native=True,
                        parent_id=None,
                    )
                    db.add(native_asset)
                    await db.flush()
                    native_cache[blockchain] = native_asset.id
                    created += 1
                    log.info(f"✅ Created native: {currency} on {blockchain}")

            await db.commit()
            log.info(
                f"✅ Phase 1 complete: {created} created, {updated} updated, {skipped} skipped"
            )

            phase2_created = 0
            phase2_updated = 0
            phase2_skipped = 0

            # phase 2: токены
            log.info("🟢 Phase 2: Loading token assets...")
            for contract_info in token_contracts:
                contract_address = contract_info.get("contract_address")
                symbol = contract_info.get("name", "")

                if not contract_address:
                    log.warning(f"⚠️ Token {symbol} has no contract_address, skipping")
                    phase2_skipped += 1
                    continue

                fb_asset = fb_by_address.get(contract_address.lower())
                if not fb_asset:
                    fb_asset = fb_by_id.get(symbol)

                if not fb_asset:
                    log.warning(f"⚠️ Fireblocks asset not found for {symbol} ({contract_address})")
                    phase2_skipped += 1
                    continue

                fb_asset_id = fb_asset.get("id", "")
                metadata = parse_fireblocks_asset(fb_asset_id, fb_asset)
                if not metadata:
                    log.warning(f"⚠️ Cannot parse metadata for {symbol}")
                    phase2_skipped += 1
                    continue

                blockchain = metadata["blockchain"]
                currency = metadata["currency"]
                network = metadata["network"]
                testnet_name = _get_testnet_name(is_testnet, blockchain)
                decimals = fb_asset.get("decimals", 18)

                fb_contract = fb_asset.get("contractAddress") or fb_asset.get("issuerAddress") or contract_address

                # линкуем к родительскому нативному ассету
                parent_id = native_cache.get(blockchain)
                if not parent_id:
                    stmt = select(AssetModel).where(
                        AssetModel.provider == "fireblocks",
                        AssetModel.blockchain == blockchain,
                        AssetModel.is_native.is_(True),
                    )
                    if testnet_name:
                        stmt = stmt.where(AssetModel.testnet == testnet_name)
                    else:
                        stmt = stmt.where(AssetModel.testnet.is_(None))
                    result = await db.execute(stmt)
                    native_asset = result.scalar_one_or_none()
                    if native_asset:
                        parent_id = native_asset.id
                        native_cache[blockchain] = parent_id

                stmt = select(AssetModel).where(
                    AssetModel.provider == "fireblocks",
                    AssetModel.contract_address == fb_contract,
                )
                if testnet_name:
                    stmt = stmt.where(AssetModel.testnet == testnet_name)
                else:
                    stmt = stmt.where(AssetModel.testnet.is_(None))
                    
                result = await db.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.asset = fb_asset_id
                    existing.symbol = currency
                    existing.display_name = contract_info.get("display_name", currency)
                    existing.blockchain = blockchain
                    existing.network = network
                    existing.decimals = decimals
                    existing.is_active = True
                    existing.is_native = False
                    existing.parent_id = parent_id
                    phase2_updated += 1
                    log.debug(f"🔄 Updated token: {currency} ({fb_contract})")
                else:
                    token_asset = AssetModel(
                        asset=fb_asset_id,
                        provider="fireblocks",
                        symbol=currency,
                        display_name=contract_info.get("display_name", currency),
                        blockchain=blockchain,
                        network=network,
                        contract_address=fb_contract,
                        testnet=testnet_name,
                        decimals=decimals,
                        is_active=True,
                        is_native=False,
                        parent_id=parent_id,
                    )
                    db.add(token_asset)
                    phase2_created += 1
                    log.info(f"✅ Created token: {currency} ({fb_contract})")

            await db.commit()
            log.info(
                f"✅ Phase 2 complete: {phase2_created} created, {phase2_updated} updated, {phase2_skipped} skipped"
            )

            total_created = created + phase2_created
            total_updated = updated + phase2_updated
            total_skipped = skipped + phase2_skipped
            log.info(
                f"🎉 Assets sync complete: {total_created} created, {total_updated} updated, {total_skipped} skipped"
            )

    except Exception as e:
        log.error(f"❌ Failed to sync assets: {e}", exc_info=True)
        # не валим старт - assets можно синкнуть вручную позже
