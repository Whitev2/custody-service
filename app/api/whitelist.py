"""Whitelist API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.whitelist import (
    WhitelistAddRequest,
    WhitelistAddResponse,
    WhitelistCheckRequest,
    WhitelistCheckResponse,
    WhitelistListResponse,
)
from app.models import VaultModel, AssetModel
from app.services.custody import get_provider
from app.storage.database import get_db
from app.config import log

router = APIRouter(prefix="/whitelist", tags=["Whitelist"])


@router.post("/add", response_model=WhitelistAddResponse)
async def add_whitelist_address(
    request: WhitelistAddRequest, db: AsyncSession = Depends(get_db)
):
    vault = await db.get(VaultModel, request.vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    asset = await db.get(AssetModel, request.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        provider = get_provider()
        result = await provider.add_whitelist_address(
            vault.provider_vault_id,
            asset.asset,
            request.address,
            request.description or "",
        )
        return WhitelistAddResponse(
            whitelist_id=result.get("id", ""),
            vault_id=request.vault_id,
            asset_id=request.asset_id,
            address=request.address,
            status="added",
        )
    except Exception as e:
        log.error(f"Error adding to whitelist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list", response_model=WhitelistListResponse)
async def list_whitelist_addresses(
    vault_id: UUID = Query(..., description="Vault ID"),
    asset_id: UUID = Query(None, description="Optional: Filter by Asset ID"),
    db: AsyncSession = Depends(get_db),
):
    vault = await db.get(VaultModel, vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    asset_fireblocks_id = None
    if asset_id:
        asset = await db.get(AssetModel, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        asset_fireblocks_id = asset.asset

    try:
        provider = get_provider()
        addresses = await provider.get_whitelist_addresses(
            vault.provider_vault_id, asset_fireblocks_id
        )
        return WhitelistListResponse(addresses=addresses, total=len(addresses))
    except Exception as e:
        log.error(f"Error listing whitelist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check", response_model=WhitelistCheckResponse)
async def check_whitelist_address(
    request: WhitelistCheckRequest, db: AsyncSession = Depends(get_db)
):
    vault = await db.get(VaultModel, request.vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    asset = await db.get(AssetModel, request.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        provider = get_provider()
        addresses = await provider.get_whitelist_addresses(
            vault.provider_vault_id, asset.asset
        )
        is_whitelisted = any(
            addr.get("address") == request.address for addr in addresses
        )
        whitelist_id = None
        if is_whitelisted:
            for addr in addresses:
                if addr.get("address") == request.address:
                    whitelist_id = addr.get("id")
                    break

        return WhitelistCheckResponse(
            address=request.address,
            is_whitelisted=is_whitelisted,
            whitelist_id=whitelist_id,
        )
    except Exception as e:
        log.error(f"Error checking whitelist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{whitelist_id}")
async def remove_whitelist_address(
    whitelist_id: str,
    vault_id: UUID = Query(..., description="Vault ID"),
    db: AsyncSession = Depends(get_db),
):
    vault = await db.get(VaultModel, vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    try:
        provider = get_provider()
        await provider.remove_whitelist_address(vault.provider_vault_id, whitelist_id)
        return {"status": "deleted", "whitelist_id": whitelist_id}
    except Exception as e:
        log.error(f"Error removing from whitelist: {e}")
        raise HTTPException(status_code=500, detail=str(e))
