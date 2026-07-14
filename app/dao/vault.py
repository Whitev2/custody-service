"""Vault DAO - database operations for vaults."""

import asyncio
from uuid import UUID, uuid4

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import log, cfg
from app.enums import VaultStatusEnum, VaultTypeEnum
from app.models import VaultModel, WalletModel, AssetModel
from app.services.custody.factory import get_provider
from app.dao.asset import activate_asset_for_vault


def _get_provider_vault_name(vault: dict) -> str:
    value = vault.get("name")
    if isinstance(value, str) and value:
        return value
    value = vault.get("accountName")
    if isinstance(value, str) and value:
        return value
    return ""


def _get_provider_vault_id(vault: dict) -> str:
    value = vault.get("id")
    if value is not None and value != "":
        return str(value)
    value = vault.get("vaultAccountId")
    if value is not None and value != "":
        return str(value)
    return ""


def _normalize_vault_name(name: str) -> str:
    name = name.strip().lower()
    return "".join(ch for ch in name if ch.isalnum() or ch == "_")


def _vault_names_match(provider_name: str, expected_name: str) -> bool:
    if provider_name == expected_name:
        return True
    if provider_name.strip().lower() == expected_name.strip().lower():
        return True
    p = _normalize_vault_name(provider_name)
    e = _normalize_vault_name(expected_name)
    if p == e:
        return True
    # Tolerate truncation: Fireblocks may shorten long vault names in some UIs/exports.
    # Only allow prefix match when it's sufficiently specific.
    if len(p) >= 12 and (e.startswith(p) or p.startswith(e)):
        return True
    return False


async def _find_vault_by_name(db: AsyncSession, name: str) -> VaultModel | None:
    """Find vault by name in local DB."""
    stmt = select(VaultModel).where(VaultModel.name == name)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _sync_vault_from_provider(
    db: AsyncSession,
    name: str,
    provider_vault_id: str,
    vault_type: str = VaultTypeEnum.REGULAR.value,
) -> VaultModel:
    """Sync existing vault from provider to local DB."""
    stmt = select(VaultModel).where(VaultModel.provider_vault_id == provider_vault_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    vault = VaultModel(
        provider_vault_id=provider_vault_id,
        name=name,
        vault_type=vault_type,
        status=VaultStatusEnum.AVAILABLE.value,
        is_active=True,
    )
    db.add(vault)
    try:
        await db.commit()
        await db.refresh(vault)
    except IntegrityError:
        await db.rollback()
        stmt = select(VaultModel).where(
            VaultModel.provider_vault_id == provider_vault_id
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return existing
        raise

    log.info(
        f"✅ Synced existing vault from provider: {vault.id}, name={name}, type={vault_type}"
    )
    return vault


async def _find_provider_vault_by_name(name: str) -> dict | None:
    """Find vault by name in provider (Fireblocks)."""
    provider = get_provider()
    # Fast path: provider-side filtering
    try:
        vaults = await provider.get_vaults(name_prefix=name)
        for vault in vaults:
            if _vault_names_match(_get_provider_vault_name(vault), name):
                return vault
        log.warning(
            f"Provider vault '{name}' not found via filtered listing (exact). returned={len(vaults)}"
        )
    except Exception as e:
        log.warning(f"Provider vault lookup by namePrefix failed: {e}")

    # Second attempt: broader prefix (e.g. USER_*)
    if "_" in name:
        broad_prefix = f"{name.split('_', 1)[0]}_"
        if broad_prefix and broad_prefix != name:
            try:
                vaults = await provider.get_vaults(name_prefix=broad_prefix)
                for vault in vaults:
                    if _vault_names_match(_get_provider_vault_name(vault), name):
                        return vault
                log.warning(
                    (
                        f"Provider vault '{name}' not found via filtered listing "
                        f"(broad={broad_prefix}). returned={len(vaults)}"
                    )
                )
            except Exception as e:
                log.warning(f"Provider vault lookup by broad namePrefix failed: {e}")

    # Slow path: full scan (should be rare; used as a fallback for 9004 sync)
    try:
        vaults = await provider.get_vaults()
        for vault in vaults:
            if _vault_names_match(_get_provider_vault_name(vault), name):
                return vault
        sample_keys = list(vaults[0].keys()) if vaults else []
        log.warning(
            f"Provider vault '{name}' not found. scanned={len(vaults)} sample_keys={sample_keys}"
        )
    except Exception as e:
        log.warning(f"Provider vault lookup full scan failed: {e}")

    return None


async def _activate_assets_async(
    db: AsyncSession, vault: VaultModel, assets: list[dict], batch_size: int = 10
) -> None:
    """Activate multiple assets in parallel with batching."""

    async def activate_single(asset_data: dict) -> tuple[dict, Exception | None]:
        """Activate single asset and return result."""
        try:
            await activate_asset_for_vault(db, vault, asset_data)
            return (asset_data, None)
        except Exception as e:
            return (asset_data, e)

    # Process assets in batches to avoid overwhelming the API
    total_assets = len(assets)
    log.info(f"Activating {total_assets} assets in parallel (batch_size={batch_size})")

    for i in range(0, total_assets, batch_size):
        batch = assets[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total_assets + batch_size - 1) // batch_size

        log.debug(f"Processing batch {batch_num}/{total_batches} ({len(batch)} assets)")

        # Run batch in parallel
        results = await asyncio.gather(
            *[activate_single(asset) for asset in batch], return_exceptions=False
        )

        # Log results
        for asset_data, error in results:
            if error:
                error_msg = str(error).lower()
                if "already exists" in error_msg or "already activated" in error_msg:
                    log.debug(
                        f"Asset {asset_data} already activated in vault {vault.name}"
                    )
                else:
                    log.warning(
                        f"Failed to activate asset {asset_data} in vault {vault.name}: {error}"
                    )

        # Commit after each batch
        await db.commit()

    log.info(f"✅ Completed activation of {total_assets} assets")


async def create_vault(
    db: AsyncSession,
    name: str | None = None,
    auto_fuel: bool = True,
    vault_type: str = VaultTypeEnum.REGULAR.value,
    assets: list[dict] | None = None,
) -> VaultModel:
    """Create new vault.

    Args:
        db: Database session
        name: Vault name (auto-generated if not provided)
        auto_fuel: Enable auto fuel
        vault_type: Type of vault
        assets: Optional list of assets [{blockchain, contract_address}, ...].
            contract_address=null for native coins.
            If not provided, creates empty vault (add assets via /asset/create).
    """
    is_testnet = cfg.app.is_testnet
    # Generate name if not provided
    if not name:
        name = f"VAULT_{uuid4().hex[:8].upper()}"

    # Assets must be explicitly provided - we don't auto-add all 1000+ assets
    if assets is None:
        assets = []
        log.info(f"Creating vault {name} without assets (add them via /asset/create)")
    
    # Convert {blockchain, contract_address} to {blockchain, currency, network} for activate_asset_for_vault
    if assets and len(assets) > 0:
        resolved_assets = []
        for asset_data in assets:
            blockchain = asset_data.get("blockchain")
            contract_address = asset_data.get("contract_address")
            
            # Find asset in DB by blockchain + contract_address
            if contract_address:
                stmt = select(AssetModel).where(
                    AssetModel.blockchain.ilike(blockchain),
                    AssetModel.contract_address == contract_address,
                    AssetModel.is_active,
                )
            else:
                # Native coin - contract_address IS NULL
                stmt = select(AssetModel).where(
                    AssetModel.blockchain.ilike(blockchain),
                    AssetModel.contract_address.is_(None),
                    AssetModel.is_active,
                )
            
            result = await db.execute(stmt)
            asset_model = result.scalar_one_or_none()
            
            if asset_model:
                resolved_assets.append({
                    "blockchain": asset_model.blockchain,
                    "currency": asset_model.currency,
                    "network": asset_model.network,
                    "is_testnet": is_testnet,
                })
            else:
                search_key = contract_address or "native"
                log.warning(f"Asset {blockchain}/{search_key} not found in custody DB")
        
        assets = resolved_assets
        log.info(f"Resolved {len(assets)} assets for vault {name}")

    # Check if vault already exists in local DB
    existing_vault = await _find_vault_by_name(db, name)
    if existing_vault:
        log.info(f"Vault already exists in DB: {existing_vault.id}, name={name}")

        # Sync assets for existing vault in parallel
        await _activate_assets_async(db, existing_vault, assets)

        # Load wallets and return
        stmt = (
            select(VaultModel)
            .where(VaultModel.id == existing_vault.id)
            .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
        )
        result = await db.execute(stmt)
        return result.scalar_one()

    # Create vault using provider
    provider = get_provider()
    try:
        fb_vault = await provider.create_vault(name, auto_fuel)
    except Exception as e:
        error_msg = str(e)
        # Handle "vault already exists" error from Fireblocks
        if "already exists" in error_msg.lower() or "9004" in error_msg:
            log.warning(f"Vault '{name}' already exists in provider, syncing...")

            # Find vault in Fireblocks by name
            provider_vault = await _find_provider_vault_by_name(name)
            if provider_vault:
                provider_vault_id = _get_provider_vault_id(provider_vault)
                if not provider_vault_id:
                    raise RuntimeError(f"Provider vault '{name}' has no id")

                vault = await _sync_vault_from_provider(
                    db, name, str(provider_vault_id), vault_type
                )

                # Sync all assets for existing vault in parallel
                await _activate_assets_async(db, vault, assets)

                # Load wallets
                stmt = (
                    select(VaultModel)
                    .where(VaultModel.id == vault.id)
                    .options(
                        selectinload(VaultModel.wallets).selectinload(WalletModel.asset)
                    )
                )
                result = await db.execute(stmt)
                return result.scalar_one()
            else:
                msg = (
                    f"Fireblocks reports vault '{name}' already exists (9004), "
                    "but custody could not find it via vault listing to sync."
                )
                log.error(msg)
                raise RuntimeError(msg) from e
        else:
            raise

    # Create vault in DB
    vault = VaultModel(
        provider_vault_id=fb_vault["id"],
        name=name,
        vault_type=vault_type,
        status=VaultStatusEnum.AVAILABLE.value,
        is_active=True,
    )
    db.add(vault)
    await db.flush()

    # Activate assets in parallel
    await _activate_assets_async(db, vault, assets)

    # Load wallets with asset info
    stmt = (
        select(VaultModel)
        .where(VaultModel.id == vault.id)
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
    )
    result = await db.execute(stmt)
    vault = result.scalar_one()

    log.info(f"✅ Vault created: {vault.id}, provider_id={vault.provider_vault_id}")
    return vault


async def get_vault_info(db: AsyncSession, vault_id: UUID) -> VaultModel | None:
    """Get vault information with wallets."""

    stmt = (
        select(VaultModel)
        .where(VaultModel.id == vault_id)
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_vaults(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> tuple[list[VaultModel], int]:
    """List all vaults with wallets."""

    # Get total count
    count_stmt = select(func.count(VaultModel.id)).where(VaultModel.is_active.is_(True))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # Get vaults with wallets loaded
    stmt = (
        select(VaultModel)
        .where(VaultModel.is_active.is_(True))
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    vaults = list(result.scalars().all())

    return vaults, total
