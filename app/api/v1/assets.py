"""Asset Admin API v1 - CRUD канонических ассетов.

Ассеты provider-agnostic. Fireblocks asset id резолвится динамически
по contract_address (токены) или blockchain (нативные).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssetModel
from app.storage import get_db
from app.config import log
from app.api.v1.schemas import (
    AssetCreateRequest,
    AssetUpdateRequest,
    AssetResponse,
    AssetListResponse,
    FireblocksAssetResponse,
    AssetLookupRequest,
)

router = APIRouter(prefix="/assets", tags=["Assets"])


def _asset_to_response(asset: AssetModel) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        symbol=asset.symbol,
        display_name=asset.display_name,
        blockchain=asset.blockchain,
        contract_address=asset.contract_address,
        network=asset.network,
        decimals=asset.decimals,
        testnet=asset.testnet,
        is_native=asset.is_native,
        is_active=asset.is_active,
        parent_id=asset.parent_id,
        created_at=asset.created_at,
    )


@router.post("/", response_model=AssetResponse, summary="Create asset")
async def create_asset(
    request: AssetCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    # дубликат: токен - по contract+testnet, нативный - по blockchain+is_native+testnet
    if request.contract_address:
        stmt = select(AssetModel).where(
            AssetModel.contract_address == request.contract_address,
            AssetModel.testnet == request.testnet,
        )
    else:
        stmt = select(AssetModel).where(
            AssetModel.blockchain == request.blockchain.upper(),
            AssetModel.is_native.is_(True),
            AssetModel.testnet == request.testnet,
        )
    
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Asset already exists: {existing.symbol} ({existing.id})",
        )
    
    asset = AssetModel(
        symbol=request.symbol.upper(),
        display_name=request.display_name,
        blockchain=request.blockchain.upper(),
        contract_address=request.contract_address,
        network=request.network.upper(),
        decimals=request.decimals,
        testnet=request.testnet.upper() if request.testnet else None,
        is_native=request.is_native,
        parent_id=request.parent_id,
    )
    
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    
    log.info(
        f"Created asset: {asset.symbol} on {asset.blockchain}",
        extra={"asset_id": str(asset.id), "blockchain": asset.blockchain},
    )
    
    return _asset_to_response(asset)


@router.get("/", response_model=AssetListResponse, summary="List assets")
async def list_assets(
    blockchain: str | None = Query(None, description="Filter by blockchain"),
    include_inactive: bool = Query(False, description="Include inactive assets"),
    testnet: str | None = Query(None, description="Filter by testnet"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AssetModel)

    if blockchain:
        stmt = stmt.where(AssetModel.blockchain == blockchain.upper())
    
    if not include_inactive:
        stmt = stmt.where(AssetModel.is_active.is_(True))
    
    if testnet:
        stmt = stmt.where(AssetModel.testnet == testnet.upper())
    
    stmt = stmt.order_by(AssetModel.symbol)
    
    result = await db.execute(stmt)
    assets = result.scalars().all()
    
    return AssetListResponse(
        assets=[_asset_to_response(a) for a in assets],
        total=len(assets),
    )


@router.get("/{asset_id}", response_model=AssetResponse, summary="Get asset")
async def get_asset(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AssetModel).where(AssetModel.id == asset_id)
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    return _asset_to_response(asset)


@router.patch("/{asset_id}", response_model=AssetResponse, summary="Update asset")
async def update_asset(
    asset_id: UUID,
    request: AssetUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AssetModel).where(AssetModel.id == asset_id)
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(asset, field, value)
    
    await db.commit()
    await db.refresh(asset)
    
    log.info(f"Updated asset: {asset.symbol}", extra={"asset_id": str(asset.id)})
    
    return _asset_to_response(asset)


@router.get("/lookup/by-contract/{contract_address}", response_model=AssetResponse, summary="Lookup by contract")
async def lookup_by_contract(
    contract_address: str,
    testnet: str | None = Query(None, description="Testnet name"),
    db: AsyncSession = Depends(get_db),
):
    """Найти канонический ассет по contract_address (для provider resolution)."""
    stmt = select(AssetModel).where(AssetModel.contract_address == contract_address)
    
    if testnet:
        stmt = stmt.where(AssetModel.testnet == testnet.upper())
    else:
        stmt = stmt.where(AssetModel.testnet.is_(None))
    
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()
    
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    return _asset_to_response(asset)


@router.get("/lookup/native/{blockchain}", response_model=AssetResponse, summary="Lookup native asset")
async def lookup_native_asset(
    blockchain: str,
    testnet: str | None = Query(None, description="Testnet name"),
    db: AsyncSession = Depends(get_db),
):
    """Нативный ассет блокчейна (ETH для ETHEREUM, BTC для BITCOIN, TRX для TRON)."""
    stmt = select(AssetModel).where(
        AssetModel.blockchain == blockchain.upper(),
        AssetModel.is_native.is_(True),
    )
    
    if testnet:
        stmt = stmt.where(AssetModel.testnet == testnet.upper())
    else:
        stmt = stmt.where(AssetModel.testnet.is_(None))
    
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()
    
    if not asset:
        raise HTTPException(status_code=404, detail="Native asset not found")
    
    return _asset_to_response(asset)


@router.post("/resolve/fireblocks", response_model=FireblocksAssetResponse, summary="Resolve Fireblocks asset ID")
async def resolve_fireblocks_asset(
    request: AssetLookupRequest,
    db: AsyncSession = Depends(get_db),
):
    # ключевой endpoint для интеграции провайдера: канонический ассет -> Fireblocks asset id
    from app.services.custody.fireblocks.service import fireblocks_service

    if request.contract_address:
        stmt = select(AssetModel).where(
            AssetModel.contract_address == request.contract_address,
            AssetModel.is_active.is_(True),
        )
    else:
        stmt = select(AssetModel).where(
            AssetModel.blockchain == request.blockchain.upper(),
            AssetModel.is_native.is_(True),
            AssetModel.is_active.is_(True),
        )
    
    if request.testnet:
        stmt = stmt.where(AssetModel.testnet == request.testnet.upper())
    else:
        stmt = stmt.where(AssetModel.testnet.is_(None))
    
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()
    
    if not asset:
        raise HTTPException(
            status_code=404, 
            detail=f"Asset not found for blockchain={request.blockchain}, contract={request.contract_address}"
        )

    is_testnet = request.testnet is not None
    fb_service = fireblocks_service()
    
    fb_asset = await fb_service.find_asset_by_contract_or_currency(
        currency=asset.symbol,
        contract_address=asset.contract_address,
        is_testnet=is_testnet,
    )
    
    if not fb_asset:
        raise HTTPException(
            status_code=404,
            detail=f"Fireblocks asset not found for {asset.symbol} on {asset.blockchain}"
        )
    
    fireblocks_asset_id = fb_asset.get("id", "")
    
    log.info(
        f"Resolved Fireblocks asset: {asset.symbol} -> {fireblocks_asset_id}",
        extra={
            "asset_id": str(asset.id),
            "fireblocks_asset_id": fireblocks_asset_id,
            "contract_address": asset.contract_address,
        }
    )
    
    return FireblocksAssetResponse(
        asset_id=asset.id,
        symbol=asset.symbol,
        blockchain=asset.blockchain,
        fireblocks_asset_id=fireblocks_asset_id,
    )


@router.get("/resolve/fireblocks/{asset_id}", response_model=FireblocksAssetResponse, summary="Resolve by asset ID")
async def resolve_fireblocks_by_id(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Fireblocks asset id по известному asset_id из нашей БД."""
    from app.services.custody.fireblocks.service import fireblocks_service

    stmt = select(AssetModel).where(AssetModel.id == asset_id)
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    is_testnet = asset.testnet is not None
    fb_service = fireblocks_service()
    
    fb_asset = await fb_service.find_asset_by_contract_or_currency(
        currency=asset.symbol,
        contract_address=asset.contract_address,
        is_testnet=is_testnet,
    )
    
    if not fb_asset:
        raise HTTPException(
            status_code=404,
            detail=f"Fireblocks asset not found for {asset.symbol}"
        )
    
    return FireblocksAssetResponse(
        asset_id=asset.id,
        symbol=asset.symbol,
        blockchain=asset.blockchain,
        fireblocks_asset_id=fb_asset.get("id", ""),
    )
