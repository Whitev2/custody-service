"""Asset DAO - database operations."""

from uuid import UUID, uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssetModel, WalletModel, VaultModel, TransactionModel
from app.services.custody import get_provider
from app.config import log


async def _resolve_fireblocks_id(asset: AssetModel) -> str:
    from app.services.custody.fireblocks.resolver import resolve_fireblocks_asset
    
    fb_id = await resolve_fireblocks_asset(asset)
    if not fb_id:
        raise ValueError(
            f"Cannot resolve Fireblocks ID for {asset.symbol} on {asset.blockchain}"
        )
    return fb_id


async def _find_asset_by_params(
    db: AsyncSession, 
    blockchain: str, 
    currency: str, 
    network: str,
    testnet: str | None = None,
) -> AssetModel | None:
    stmt = select(AssetModel).where(
        AssetModel.blockchain == blockchain.upper(),
        AssetModel.symbol == currency.upper(),
        AssetModel.is_active.is_(True),
    )
    
    if testnet:
        stmt = stmt.where(AssetModel.testnet == testnet.upper())
    else:
        stmt = stmt.where(AssetModel.testnet.is_(None))
    
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def activate_asset_for_vault(
    db: AsyncSession, vault: VaultModel, asset_data: dict
) -> WalletModel:
    blockchain = asset_data["blockchain"]
    currency = asset_data["currency"]
    network = asset_data["network"]
    testnet = asset_data.get("testnet")

    # bool is_testnet → имя testnet-сети
    if asset_data.get("is_testnet") and not testnet:
        testnet_map = {
            "ETHEREUM": "SEPOLIA",
            "TRON": "SHASTA", 
            "BITCOIN": "TESTNET",
        }
        testnet = testnet_map.get(blockchain.upper(), "TESTNET")

    asset = await _find_asset_by_params(db, blockchain, currency, network, testnet)

    if not asset:
        raise ValueError(f"Asset not found: {blockchain}/{currency}/{network}")

    stmt = select(WalletModel).where(
        WalletModel.vault_id == vault.id, WalletModel.asset_id == asset.id
    )
    result = await db.execute(stmt)
    existing_wallet = result.scalar_one_or_none()

    fb_asset_id = await _resolve_fireblocks_id(asset)

    provider = get_provider()
    try:
        fb_response = await provider.activate_asset(
            vault.provider_vault_id, fb_asset_id
        )
        log.debug(
            f"Activated asset {fb_asset_id} in vault {vault.provider_vault_id}: {fb_response}"
        )
    except Exception as e:
        error_msg = str(e).lower()
        if "already exists" in error_msg:
            # уже активирован - тянем инфу с адресом
            log.debug(
                f"Asset {fb_asset_id} already exists in vault {vault.provider_vault_id}, fetching asset info"
            )
            fb_response = await provider.get_vault_asset_info(
                vault.provider_vault_id, fb_asset_id
            )
            if fb_response:
                log.debug(
                    f"Got asset info for {fb_asset_id}: address={fb_response.get('address')}"
                )
            else:
                log.warning(
                    f"Asset {fb_asset_id} not found in vault {vault.provider_vault_id}"
                )
                fb_response = {}
        else:
            raise

    address = fb_response.get("address", "")
    if not address:
        log.warning(
            f"Empty address for asset {fb_asset_id} in vault {vault.provider_vault_id}. "
            f"Response: {fb_response}"
        )

    if existing_wallet:
        log.debug(
            f"Updating existing wallet {existing_wallet.id} with fresh data from Fireblocks"
        )
        existing_wallet.address = address
        existing_wallet.legacy_address = fb_response.get("legacyAddress")
        existing_wallet.tag = fb_response.get("tag")
        return existing_wallet

    # whitelist адреса для hot/warm/cold
    if vault.vault_type in ("hot", "warm", "cold") and address:
        try:
            await provider.add_whitelist_address(
                vault.provider_vault_id,
                fb_asset_id,
                address,
            )
        except Exception as e:
            log.warning(
                f"Failed to add whitelist address for vault {vault.id} ({vault.vault_type}): {e}"
            )

    wallet = WalletModel(
        id=uuid4(),
        vault_id=vault.id,
        asset_id=asset.id,
        address=address,
        legacy_address=fb_response.get("legacyAddress"),
        tag=fb_response.get("tag"),
        balance=0,
    )
    db.add(wallet)

    log.info(
        f"✅ Created new wallet {wallet.id} for asset {asset.symbol} in vault {vault.name}"
    )
    return wallet


async def create_asset_in_vault(
    db: AsyncSession, vault_id: UUID, asset_id: UUID
) -> WalletModel:
    vault = await db.get(VaultModel, vault_id)
    if not vault:
        raise ValueError(f"Vault {vault_id} not found")

    asset = await db.get(AssetModel, asset_id)
    if not asset:
        raise ValueError(f"Asset {asset_id} not found")

    stmt = select(WalletModel).where(
        WalletModel.vault_id == vault_id, WalletModel.asset_id == asset_id
    )
    result = await db.execute(stmt)
    existing_wallet = result.scalar_one_or_none()

    if existing_wallet:
        return existing_wallet

    fb_asset_id = await _resolve_fireblocks_id(asset)

    provider = get_provider()
    fb_response = await provider.activate_asset(vault.provider_vault_id, fb_asset_id)

    address = fb_response.get("address", "")
    if vault.vault_type in ("hot", "warm", "cold") and address:
        try:
            await provider.add_whitelist_address(
                vault.provider_vault_id,
                fb_asset_id,
                address,
            )
        except Exception as e:
            log.warning(
                f"Failed to add whitelist address for vault {vault.id} ({vault.vault_type}): {e}"
            )

    wallet = WalletModel(
        id=uuid4(),
        vault_id=vault.id,
        asset_id=asset.id,
        address=fb_response.get("address", ""),
        legacy_address=fb_response.get("legacyAddress"),
        tag=fb_response.get("tag"),
        balance=0,
    )
    db.add(wallet)
    await db.flush()

    await db.commit()
    return wallet


async def get_asset_info(db: AsyncSession, asset_id: UUID) -> AssetModel | None:
    return await db.get(AssetModel, asset_id)


async def get_asset_history(
    db: AsyncSession, asset_id: UUID, skip: int = 0, limit: int = 100
) -> tuple[list[TransactionModel], int]:
    count_stmt = select(func.count(TransactionModel.id)).where(
        TransactionModel.asset_id == asset_id
    )
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    stmt = (
        select(TransactionModel)
        .where(TransactionModel.asset_id == asset_id)
        .order_by(TransactionModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    transactions = list(result.scalars().all())

    return transactions, total


async def get_asset_addresses(db: AsyncSession, asset_id: UUID) -> list[WalletModel]:
    from sqlalchemy.orm import selectinload

    stmt = (
        select(WalletModel)
        .where(WalletModel.asset_id == asset_id)
        .options(selectinload(WalletModel.vault))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def find_asset_by_contract(
    db: AsyncSession, 
    contract_address: str,
    testnet: str | None = None,
) -> AssetModel | None:
    stmt = select(AssetModel).where(
        AssetModel.contract_address == contract_address,
        AssetModel.is_active.is_(True),
    )
    
    if testnet:
        stmt = stmt.where(AssetModel.testnet == testnet.upper())
    else:
        stmt = stmt.where(AssetModel.testnet.is_(None))
    
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def find_native_asset(
    db: AsyncSession, 
    blockchain: str,
    testnet: str | None = None,
) -> AssetModel | None:
    stmt = select(AssetModel).where(
        AssetModel.blockchain == blockchain.upper(),
        AssetModel.is_native.is_(True),
        AssetModel.is_active.is_(True),
    )
    
    if testnet:
        stmt = stmt.where(AssetModel.testnet == testnet.upper())
    else:
        stmt = stmt.where(AssetModel.testnet.is_(None))
    
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
