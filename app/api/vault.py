from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.vault import (
    VaultCreateRequest,
    VaultCreateResponse,
    VaultInfoResponse,
    VaultListResponse,
    WalletBalanceInfo,
    WalletInfo,
)
from app.dao.vault import create_vault, get_vault_info, list_vaults
from app.storage.database import get_db
from app.config import log

router = APIRouter(prefix="/vault", tags=["Vault"])


@router.post("/create", summary="Создать новый vault")
async def create_vault_endpoint(
    request: VaultCreateRequest, db: AsyncSession = Depends(get_db)
) -> VaultCreateResponse:
    log.info(f"Received vault create request: name={request.name}, assets={request.assets}")
    try:
        vault = await create_vault(
            db,
            name=request.name,
            auto_fuel=request.auto_fuel,
            vault_type=request.vault_type.value,
            assets=request.assets,
        )

        wallets = []
        for wallet in vault.wallets:
            wallets.append(
                WalletInfo(
                    wallet_id=wallet.id,
                    asset_id=wallet.asset_id,
                    blockchain=wallet.asset.blockchain,
                    currency=wallet.asset.currency,
                    network=wallet.asset.network,
                    address=wallet.address,
                    legacy_address=wallet.legacy_address,
                    tag=wallet.tag,
                )
            )

        return VaultCreateResponse(
            vault_id=vault.id,
            provider_vault_id=vault.provider_vault_id,
            name=vault.name,
            vault_type=vault.vault_type,
            status=vault.status,
            wallets=wallets,
            created_at=vault.created_at,
        )
    except ValueError as e:
        log.warning(f"Bad request in create_vault: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        msg = str(e)
        log.error(f"Error creating vault: {e}")
        if "fireblocks" in msg.lower() or "api error" in msg.lower():
            raise HTTPException(status_code=502, detail=msg)
        raise HTTPException(status_code=500, detail=msg)


@router.get("/{vault_id}/info", response_model=VaultInfoResponse)
async def get_vault_info_endpoint(vault_id: UUID, db: AsyncSession = Depends(get_db)):
    vault = await get_vault_info(db, vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    wallets_data = []
    for wallet in vault.wallets:
        wallets_data.append(
            WalletBalanceInfo(
                wallet_id=wallet.id,
                asset_id=wallet.asset_id,
                blockchain=wallet.asset.blockchain,
                currency=wallet.asset.currency,
                network=wallet.asset.network,
                address=wallet.address,
                balance=wallet.balance,
            )
        )

    return VaultInfoResponse(
        vault_id=vault.id,
        provider_vault_id=vault.provider_vault_id,
        name=vault.name,
        vault_type=vault.vault_type,
        status=vault.status,
        is_active=vault.is_active,
        is_primary=vault.is_primary,
        wallets=wallets_data,
        created_at=vault.created_at,
    )


@router.get("/list", response_model=VaultListResponse)
async def list_vaults_endpoint(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    vaults, total = await list_vaults(db, skip, limit)

    vaults_data = []
    for vault in vaults:
        wallets_data = []
        for wallet in vault.wallets:
            wallets_data.append(
                WalletBalanceInfo(
                    wallet_id=wallet.id,
                    asset_id=wallet.asset_id,
                    blockchain=wallet.asset.blockchain,
                    currency=wallet.asset.currency,
                    network=wallet.asset.network,
                    address=wallet.address,
                    balance=wallet.balance,
                )
            )

        vaults_data.append(
            VaultInfoResponse(
                vault_id=vault.id,
                provider_vault_id=vault.provider_vault_id,
                name=vault.name,
                vault_type=vault.vault_type,
                status=vault.status,
                is_active=vault.is_active,
                is_primary=vault.is_primary,
                wallets=wallets_data,
                created_at=vault.created_at,
            )
        )

    return VaultListResponse(vaults=vaults_data, total=total)
