"""
Treasury bootstrap - автоматическое создание HOT кошелька при старте.
"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import log, cfg
from app.models import VaultModel, AssetModel, WalletModel
from app.enums.types import VaultTypeEnum
from app.enums.status import VaultStatusEnum
from app.services.custody import get_provider
from app.dao.asset import activate_asset_for_vault


DEFAULT_HOT_WALLET_NAME = "HOT_DEFAULT"


async def bootstrap_default_hot_wallet(db: AsyncSession) -> VaultModel | None:
    """
    Создать дефолтный HOT кошелек при первом запуске, если его нет.

    Returns:
        VaultModel если создан/существует, None если ошибка
    """
    try:
        # Проверяем есть ли уже primary HOT кошелек
        stmt = select(VaultModel).where(
            VaultModel.vault_type == VaultTypeEnum.HOT.value,
            VaultModel.is_primary.is_(True),
            VaultModel.is_active.is_(True),
        )
        result = await db.execute(stmt)
        existing_primary = result.scalar_one_or_none()

        if existing_primary:
            log.info(f"✅ Primary HOT wallet already exists: {existing_primary.name}")
            return existing_primary

        # Проверяем есть ли хотя бы один HOT кошелек
        stmt = select(VaultModel).where(
            VaultModel.vault_type == VaultTypeEnum.HOT.value,
            VaultModel.is_active.is_(True),
        )
        result = await db.execute(stmt)
        any_hot = result.scalar_one_or_none()

        if any_hot:
            # Есть HOT но не primary - делаем его primary
            any_hot.is_primary = True
            await db.commit()
            log.info(f"✅ Set existing HOT wallet as primary: {any_hot.name}")
            return any_hot

        # Нет HOT кошельков - создаём или синхронизируем дефолтный
        log.info(f"🔧 Creating default HOT wallet: {DEFAULT_HOT_WALLET_NAME}")

        provider = get_provider()

        # Пробуем создать vault в Fireblocks
        try:
            fb_vault = await provider.create_vault(
                DEFAULT_HOT_WALLET_NAME, auto_fuel=True
            )
            provider_vault_id = fb_vault["id"]
            log.info(f"✅ Created new vault in Fireblocks: {provider_vault_id}")
        except Exception as create_err:
            # Если vault уже существует в Fireblocks - найдём его
            if "already exists" in str(create_err) or "9004" in str(create_err):
                log.info(
                    f"🔍 Vault {DEFAULT_HOT_WALLET_NAME} exists in Fireblocks, searching..."
                )

                # Получаем список vaults и ищем по имени
                vaults = await provider.get_vaults(name_prefix=DEFAULT_HOT_WALLET_NAME)
                fb_vault = None
                for v in vaults:
                    if v.get("name") == DEFAULT_HOT_WALLET_NAME:
                        fb_vault = v
                        break

                if not fb_vault:
                    log.error(
                        f"❌ Cannot find vault {DEFAULT_HOT_WALLET_NAME} in Fireblocks"
                    )
                    return None

                provider_vault_id = fb_vault["id"]
                log.info(f"✅ Found existing vault in Fireblocks: {provider_vault_id}")
            else:
                raise create_err

        # Создаём vault в БД
        vault = VaultModel(
            provider_vault_id=provider_vault_id,
            name=DEFAULT_HOT_WALLET_NAME,
            vault_type=VaultTypeEnum.HOT.value,
            is_primary=True,
            status=VaultStatusEnum.AVAILABLE.value,
            is_active=True,
            description="Default HOT wallet created automatically",
        )
        db.add(vault)
        await db.commit()

        log.info(f"✅ Default HOT wallet synced: {vault.id}")
        return vault

    except Exception as e:
        log.error(f"❌ Failed to bootstrap HOT wallet: {e}")
        await db.rollback()
        return None


async def ensure_asset_in_hot_wallet(
    db: AsyncSession,
    blockchain: str,
    contract_address: str | None,
) -> bool:
    """
    Убедиться что ассет активирован в primary HOT кошельке.
    Если нет - активировать автоматически.

    Args:
        db: Database session
        blockchain: Блокчейн (ETHEREUM, TRON, etc.)
        contract_address: Адрес контракта (None для нативных)

    Returns:
        True если ассет есть/создан, False если ошибка
    """
    is_testnet = cfg.app.is_testnet

    try:
        # Находим primary HOT кошелек
        stmt = select(VaultModel).where(
            VaultModel.vault_type == VaultTypeEnum.HOT.value,
            VaultModel.is_primary.is_(True),
            VaultModel.is_active.is_(True),
        )
        result = await db.execute(stmt)
        hot_vault = result.scalar_one_or_none()

        if not hot_vault:
            log.warning("No primary HOT wallet found, cannot auto-activate asset")
            return False

        # Находим ассет в БД (case-insensitive blockchain)
        if contract_address:
            stmt = select(AssetModel).where(
                func.upper(AssetModel.blockchain) == blockchain.upper(),
                AssetModel.contract_address == contract_address,
                AssetModel.is_active.is_(True),
            )
        else:
            stmt = select(AssetModel).where(
                func.upper(AssetModel.blockchain) == blockchain.upper(),
                AssetModel.contract_address.is_(None),
                AssetModel.is_active.is_(True),
            )

        # Filter by testnet: is_testnet=True means testnet IS NOT NULL
        if is_testnet:
            stmt = stmt.where(AssetModel.testnet.isnot(None))
        else:
            stmt = stmt.where(AssetModel.testnet.is_(None))

        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()

        if not asset:
            log.warning(
                f"Asset not found in DB: blockchain={blockchain}, "
                f"contract={contract_address}"
            )
            return False

        # Проверяем есть ли уже wallet для этого ассета в HOT
        stmt = select(WalletModel).where(
            WalletModel.vault_id == hot_vault.id,
            WalletModel.asset_id == asset.id,
        )
        result = await db.execute(stmt)
        existing_wallet = result.scalar_one_or_none()

        if existing_wallet:
            log.debug(f"Asset {asset.currency} already active in HOT wallet")
            return True

        # Активируем ассет в HOT кошельке
        log.info(
            f"🔧 Auto-activating asset {asset.currency} ({blockchain}) in HOT wallet"
        )

        asset_data = {
            "blockchain": asset.blockchain,
            "currency": asset.currency,
            "network": asset.network,
            "is_testnet": is_testnet,
        }

        await activate_asset_for_vault(db, hot_vault, asset_data)
        await db.commit()

        log.info(f"✅ Asset {asset.currency} activated in HOT wallet")
        return True

    except Exception as e:
        log.error(f"❌ Failed to ensure asset in HOT wallet: {e}")
        await db.rollback()
        return False
