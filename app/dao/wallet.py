"""Wallet DAO functions."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import VaultModel, WalletModel, AssetModel
from app.services.custody.factory import get_provider
from app.dao.vault import create_vault
from app.dao.asset import activate_asset_for_vault
from app.config import log, cfg
from app.schemas.wallet import WalletWithVaultResponse


async def get_or_create_wallet_for_vault(
    db: AsyncSession,
    vault_name: str,
    currency: str,
    contract_address: str | None,
) -> WalletWithVaultResponse:
    """Получить/создать кошелёк для vault и валюты. Универсально для любых vault (USER, POOL, MERCHANT...)."""

    is_testnet = cfg.app.is_testnet

    log.info(
        f"Запрос кошелька для vault {vault_name}: "
        f"currency={currency}, contract={contract_address}, is_testnet={is_testnet}"
    )

    stmt = select(VaultModel).where(VaultModel.name == vault_name)
    result = await db.execute(stmt)
    vault = result.scalar_one_or_none()

    if not vault:
        log.info(f"Vault {vault_name} не найден, создаем новый")
        vault = await create_vault(
            db=db,
            name=vault_name,
            auto_fuel=True,
            vault_type="regular",
            assets=[],  # vault без ассетов
        )

    provider = get_provider()
    asset_info = await provider.find_asset_by_contract_or_currency(
        currency=currency,
        contract_address=contract_address,
        is_testnet=is_testnet,
    )

    if not asset_info:
        raise ValueError(
            f"Asset не найден в Fireblocks: currency={currency}, "
            f"contract_address={contract_address}"
        )

    asset_id = asset_info.get("id")
    blockchain = asset_info.get("blockchain")
    # Fireblocks всегда отдаёт type (BEP20, TRON_TRC20 и т.п.) — используем его как network
    network = asset_info.get("type")

    if not all([asset_id, blockchain, network]):
        raise ValueError(f"Неполные данные asset из Fireblocks: {asset_info}")

    log.info(
        f"Найден asset в Fireblocks: id={asset_id}, "
        f"blockchain={blockchain}, network={network}"
    )

    # asset в локальной БД по Fireblocks asset ID
    stmt = select(AssetModel).where(
        AssetModel.provider == "fireblocks",
        AssetModel.asset == asset_id,
    )
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if not asset:
        raise ValueError(
            f"Asset {asset_id} не найден в локальной БД (provider=fireblocks). Требуется синхронизация."
        )

    stmt = select(WalletModel).where(
        WalletModel.vault_id == vault.id,
        WalletModel.asset_id == asset.id,
    )
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()

    if not wallet:
        log.info(f"Активируем asset {asset_id} в vault {vault_name}")
        asset_data = {
            "blockchain": blockchain,
            "currency": currency,
            "network": network,
            "is_testnet": asset_info.get("is_testnet", is_testnet),
        }
        wallet = await activate_asset_for_vault(db, vault, asset_data)

    return WalletWithVaultResponse(
        wallet_id=wallet.id,
        vault_id=vault.id,
        asset_id=asset_id,
        blockchain=blockchain,
        currency=currency,
        network=network,
        address=wallet.address,
        legacy_address=wallet.legacy_address,
        tag=wallet.tag,
    )


async def get_or_create_wallet_for_existing_vault(
    db: AsyncSession,
    custody_vault_id: str,
    currency: str,
    contract_address: str | None,
) -> WalletWithVaultResponse:
    """Получить/создать кошелёк для существующего vault по custody_vault_id (vault и asset создаются при отсутствии)."""

    is_testnet = cfg.app.is_testnet

    log.info(
        f"Запрос кошелька для существующего vault {custody_vault_id}: "
        f"currency={currency}, contract={contract_address}, is_testnet={is_testnet}"
    )

    stmt = select(VaultModel).where(VaultModel.vault_id == custody_vault_id)
    result = await db.execute(stmt)
    vault = result.scalar_one_or_none()

    if not vault:
        log.info(
            f"Vault с custody_vault_id={custody_vault_id} не найден, создаем новый"
        )
        provider = get_provider()
        vault_info = await provider.get_vault_by_id(custody_vault_id)

        if not vault_info:
            raise ValueError(f"Vault с ID {custody_vault_id} не найден в Fireblocks")

        vault_name = vault_info.get("name", f"VAULT_{custody_vault_id}")

        vault = await create_vault(
            db=db,
            name=vault_name,
            auto_fuel=True,
            vault_type="regular",
            assets=[],
            provider_vault_id=custody_vault_id,
        )

    provider = get_provider()
    asset_info = await provider.find_asset_by_contract_or_currency(
        currency=currency,
        contract_address=contract_address,
        is_testnet=is_testnet,
    )

    if not asset_info:
        raise ValueError(
            f"Asset не найден в Fireblocks: currency={currency}, "
            f"contract_address={contract_address}"
        )

    asset_id = asset_info.get("id")
    blockchain = asset_info.get("blockchain")
    network = asset_info.get("type")

    if not all([asset_id, blockchain, network]):
        raise ValueError(f"Неполные данные asset из Fireblocks: {asset_info}")

    log.info(
        f"Найден asset в Fireblocks: id={asset_id}, "
        f"blockchain={blockchain}, network={network}"
    )

    stmt = select(AssetModel).where(
        AssetModel.provider == "fireblocks",
        AssetModel.asset == asset_id,
    )
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if not asset:
        raise ValueError(
            f"Asset {asset_id} не найден в локальной БД (provider=fireblocks). Требуется синхронизация."
        )

    stmt = select(WalletModel).where(
        WalletModel.vault_id == vault.id,
        WalletModel.asset_id == asset.id,
    )
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()

    if not wallet:
        log.info(f"Активируем asset {asset_id} в vault {vault.name}")
        asset_data = {
            "blockchain": blockchain,
            "currency": currency,
            "network": network,
            "is_testnet": asset_info.get("is_testnet", is_testnet),
        }
        wallet = await activate_asset_for_vault(db, vault, asset_data)

    return WalletWithVaultResponse(
        wallet_id=wallet.id,
        vault_id=vault.id,
        asset_id=asset_id,
        blockchain=blockchain,
        currency=currency,
        network=network,
        address=wallet.address,
        legacy_address=wallet.legacy_address,
        tag=wallet.tag,
    )
