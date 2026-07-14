"""Asset API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import AssetModel
from app.schemas.asset import (
    AssetCreateRequest,
    AssetInfoResponse,
    AssetHistoryResponse,
    AssetAddressesResponse,
)
from app.dao.asset import (
    create_asset_in_vault,
    get_asset_history,
    get_asset_addresses,
)
from app.storage import get_db
from app.config import log

router = APIRouter(prefix="/asset", tags=["Asset"])


@router.post("/create", response_model=AssetInfoResponse)
async def create_asset_endpoint(
    request: AssetCreateRequest, db: AsyncSession = Depends(get_db)
):
    """Create/activate asset in vault. поиск по asset_id или blockchain+contract_address."""
    try:
        asset_id = request.asset_id

        # asset_id не указан - ищем по blockchain + contract_address (null = нативная монета)
        if not asset_id:
            if not request.blockchain:
                raise HTTPException(
                    status_code=400,
                    detail="blockchain is required when asset_id is not provided",
                )

            if request.contract_address:
                stmt = select(AssetModel).where(
                    AssetModel.blockchain.ilike(request.blockchain),
                    AssetModel.contract_address == request.contract_address,
                    AssetModel.is_active,
                )
            else:
                # нативная монета - contract_address IS NULL
                stmt = select(AssetModel).where(
                    AssetModel.blockchain.ilike(request.blockchain),
                    AssetModel.contract_address.is_(None),
                    AssetModel.is_active,
                )

            result = await db.execute(stmt)
            asset = result.scalar_one_or_none()

            if not asset:
                search_key = request.contract_address or "native"
                raise HTTPException(
                    status_code=404,
                    detail=f"Asset not found: {request.blockchain}/{search_key}",
                )
            
            log.info(
                f"Found asset by search: {asset.asset} ({asset.blockchain}/{asset.currency})",
                extra={"asset_id": str(asset.id), "contract": request.contract_address}
            )
            asset_id = asset.id

        wallet = await create_asset_in_vault(db, request.vault_id, asset_id)
        return AssetInfoResponse(
            asset_id=wallet.asset_id,
            wallet_id=wallet.id,
            vault_id=wallet.vault_id,
            address=wallet.address,
            legacy_address=wallet.legacy_address,
            tag=wallet.tag,
            balance=str(wallet.balance),
            blockchain=wallet.asset.blockchain,
            currency=wallet.asset.currency,
            network=wallet.asset.network,
            created_at=wallet.created_at,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"Error creating asset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{asset_id}/info", response_model=AssetInfoResponse)
async def get_asset_info_endpoint(
    asset_id: UUID,
    vault_id: UUID = Query(..., description="Vault ID"),
    db: AsyncSession = Depends(get_db),
):
    from app.models import WalletModel
    from sqlalchemy import select

    stmt = select(WalletModel).where(
        WalletModel.vault_id == vault_id, WalletModel.asset_id == asset_id
    )
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()

    if not wallet:
        raise HTTPException(status_code=404, detail="Asset not found in vault")

    return AssetInfoResponse(
        asset_id=wallet.asset_id,
        wallet_id=wallet.id,
        vault_id=wallet.vault_id,
        address=wallet.address,
        legacy_address=wallet.legacy_address,
        tag=wallet.tag,
        balance=str(wallet.balance),
        blockchain=wallet.asset.blockchain,
        currency=wallet.asset.currency,
        network=wallet.asset.network,
        created_at=wallet.created_at,
    )


@router.get("/{asset_id}/history", response_model=AssetHistoryResponse)
async def get_asset_history_endpoint(
    asset_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    transactions, total = await get_asset_history(db, asset_id, skip, limit)

    transactions_data = []
    for tx in transactions:
        transactions_data.append(
            {
                "tx_id": str(tx.id),
                "provider_tx_id": tx.provider_tx_id,
                "tx_hash": tx.tx_hash,
                "amount": str(tx.amount),
                "status": tx.status,
                "created_at": tx.created_at.isoformat(),
            }
        )

    return AssetHistoryResponse(transactions=transactions_data, total=total)


@router.get("/{asset_id}/addresses", response_model=AssetAddressesResponse)
async def get_asset_addresses_endpoint(
    asset_id: UUID, db: AsyncSession = Depends(get_db)
):
    wallets = await get_asset_addresses(db, asset_id)

    addresses_data = []
    for wallet in wallets:
        addresses_data.append(
            {
                "wallet_id": str(wallet.id),
                "vault_id": str(wallet.vault_id),
                "vault_name": wallet.vault.name,
                "address": wallet.address,
                "balance": str(wallet.balance),
            }
        )

    return AssetAddressesResponse(addresses=addresses_data, total=len(addresses_data))
