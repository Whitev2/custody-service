"""Common webhook functions - simplified for V2 (no business logic).

Uses canonical AssetModel with reverse resolution from Fireblocks ID.
"""

from typing import TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import log
from app.models import AssetModel, VaultModel, WalletModel
from app.schemas.webhooks import TransactionDetailsSchema


class WalletInfo(TypedDict):
    """Wallet information dictionary."""

    vault_id: UUID | None
    wallet_id: UUID | None
    asset_id: UUID | None
    blockchain: str | None
    currency: str | None


async def _find_asset_by_fireblocks_id(
    db: AsyncSession, fireblocks_asset_id: str
) -> AssetModel | None:
    """
    Find canonical asset by Fireblocks asset ID.
    
    Uses reverse resolution: Fireblocks ID -> contract_address -> AssetModel
    
    Examples:
    - USDT_ETH -> find by contract address of USDT on Ethereum
    - ETH -> find native ETH on Ethereum
    - TRX_SHASTA -> find native TRX on Tron testnet
    """
    from app.services.custody.fireblocks.service import fireblocks_service
    from app.services.custody.fireblocks.utils import parse_fireblocks_asset
    
    # 1. Get Fireblocks asset info
    fb_service = fireblocks_service()
    fb_assets = await fb_service.get_supported_assets()
    
    fb_asset = next(
        (a for a in fb_assets if a.get("id") == fireblocks_asset_id),
        None
    )
    
    if not fb_asset:
        log.warning(f"⚠️ Fireblocks asset {fireblocks_asset_id} not found in supported assets")
        return None
    
    # 2. Parse metadata
    metadata = parse_fireblocks_asset(fireblocks_asset_id, fb_asset)
    if not metadata:
        log.warning(f"⚠️ Cannot parse metadata for {fireblocks_asset_id}")
        return None
    
    blockchain = metadata.get("blockchain")
    is_testnet = metadata.get("is_testnet", False)
    
    # 3. Find by contract address (for tokens) or blockchain (for native)
    contract_address = fb_asset.get("contractAddress") or fb_asset.get("issuerAddress")
    
    if contract_address:
        # Token - find by contract address
        stmt = select(AssetModel).where(
            AssetModel.contract_address == contract_address,
            AssetModel.is_active.is_(True),
        )
    else:
        # Native asset - find by blockchain + is_native
        stmt = select(AssetModel).where(
            AssetModel.blockchain == blockchain,
            AssetModel.is_native.is_(True),
            AssetModel.is_active.is_(True),
        )
    
    # Filter by testnet
    if is_testnet:
        stmt = stmt.where(AssetModel.testnet.isnot(None))
    else:
        stmt = stmt.where(AssetModel.testnet.is_(None))
    
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()
    
    if asset:
        log.debug(f"Found asset {asset.symbol} for Fireblocks ID {fireblocks_asset_id}")
    else:
        log.warning(
            f"⚠️ Canonical asset not found for Fireblocks ID {fireblocks_asset_id}. "
            f"Blockchain={blockchain}, contract={contract_address}"
        )
    
    return asset


async def identify_wallet(
    db: AsyncSession, tx: TransactionDetailsSchema
) -> WalletInfo | None:
    """
    Identify wallet by transaction data (simplified - no business logic).

    Returns vault_id, wallet_id, asset_id based on destination vault and asset.
    """
    if not tx.destination or not tx.destination.id:
        log.warning("⚠️ No destination vault information")
        return None

    provider_vault_id = str(tx.destination.id)

    # Find vault by provider_vault_id (even if deactivated)
    vault_stmt = select(VaultModel).where(
        VaultModel.provider_vault_id == provider_vault_id
    )
    vault_result = await db.execute(vault_stmt)
    vault = vault_result.scalar_one_or_none()

    if not vault:
        log.warning(f"⚠️ Vault not found for provider_vault_id={provider_vault_id}")
        return None

    result: WalletInfo = {
        "vault_id": vault.id,
        "wallet_id": None,
        "asset_id": None,
        "blockchain": None,
        "currency": None,
    }

    # Find asset and wallet if assetId is provided
    if tx.assetId:
        # Reverse resolve: Fireblocks ID -> canonical asset
        asset = await _find_asset_by_fireblocks_id(db, tx.assetId)

        if asset:
            result["asset_id"] = asset.id
            result["blockchain"] = asset.blockchain
            result["currency"] = asset.symbol

            # Find wallet
            wallet_stmt = select(WalletModel).where(
                WalletModel.vault_id == vault.id,
                WalletModel.asset_id == asset.id,
            )
            wallet_result = await db.execute(wallet_stmt)
            wallet = wallet_result.scalar_one_or_none()

            if wallet:
                result["wallet_id"] = wallet.id

    log.info(
        f"🔍 Wallet identified: vault_id={result['vault_id']}, "
        f"wallet_id={result['wallet_id']}, asset_id={result['asset_id']}"
    )

    return result


async def identify_source_wallet(
    db: AsyncSession, tx: TransactionDetailsSchema
) -> WalletInfo | None:
    """
    Identify source wallet for outgoing transactions.

    Returns vault_id, wallet_id, asset_id based on source vault and asset.
    """
    if not tx.source or not tx.source.id:
        log.warning("⚠️ No source vault information")
        return None

    provider_vault_id = str(tx.source.id)

    # Find vault by provider_vault_id (even if deactivated)
    vault_stmt = select(VaultModel).where(
        VaultModel.provider_vault_id == provider_vault_id
    )
    vault_result = await db.execute(vault_stmt)
    vault = vault_result.scalar_one_or_none()

    if not vault:
        log.warning(
            f"⚠️ Source vault not found for provider_vault_id={provider_vault_id}"
        )
        return None

    result: WalletInfo = {
        "vault_id": vault.id,
        "wallet_id": None,
        "asset_id": None,
        "blockchain": None,
        "currency": None,
    }

    # Find asset and wallet if assetId is provided
    if tx.assetId:
        # Reverse resolve: Fireblocks ID -> canonical asset
        asset = await _find_asset_by_fireblocks_id(db, tx.assetId)

        if asset:
            result["asset_id"] = asset.id
            result["blockchain"] = asset.blockchain
            result["currency"] = asset.symbol

            # Find wallet
            wallet_stmt = select(WalletModel).where(
                WalletModel.vault_id == vault.id,
                WalletModel.asset_id == asset.id,
            )
            wallet_result = await db.execute(wallet_stmt)
            wallet = wallet_result.scalar_one_or_none()

            if wallet:
                result["wallet_id"] = wallet.id

    log.info(
        f"🔍 Source wallet identified: vault_id={result['vault_id']}, "
        f"wallet_id={result['wallet_id']}, asset_id={result['asset_id']}"
    )

    return result
