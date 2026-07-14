"""Admin API endpoints for custody."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssetModel
from app.services.custody.fireblocks import parse_fireblocks_asset
from app.storage.database import get_db
from app.services.custody import get_provider
from app.config import log

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/fireblocks-assets")
async def list_fireblocks_assets(
    search: str | None = None,
    limit: int = 100,
):
    """
    List all supported assets directly from Fireblocks.

    Use this to find correct asset IDs for mapping.
    """
    provider = get_provider()

    try:
        fb_assets = await provider.get_supported_assets()

        # Filter by search term if provided
        if search:
            search_lower = search.lower()
            fb_assets = [
                a
                for a in fb_assets
                if search_lower in a.get("id", "").lower()
                or search_lower in a.get("name", "").lower()
            ]

        # Limit results
        fb_assets = fb_assets[:limit]


        return {
            "assets": [
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "type": a.get("type"),
                    "decimals": a.get("decimals"),
                    "contractAddress": a.get("contractAddress"),
                    "blockchain": a.get("blockchain"),
                }
                for a in fb_assets
            ],
            "total": len(fb_assets),
            "hint": "Use 'id' field for ASSET_MAPPING in admin.py",
        }

    except Exception as e:
        log.error(f"Error listing Fireblocks assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sync-assets/status")
async def get_sync_status():
    """
    Get asset sync task status.
    
    Shows last sync time, interval, and whether background task is running.
    """
    from app.services.asset_sync import get_asset_sync_status
    return await get_asset_sync_status()


@router.post("/sync-assets")
async def sync_assets(
    db: AsyncSession = Depends(get_db),
    use_lock: bool = Query(True, description="Use distributed lock (recommended)"),
):
    """
    Sync supported assets from Fireblocks to database.
    
    Uses distributed lock to prevent multiple pods from syncing simultaneously.
    If use_lock=True and lock is held by another pod, returns immediately.
    """
    if use_lock:
        from app.services.asset_sync import force_sync_assets
        result = await force_sync_assets()
        if result.get("status") == "skipped":
            raise HTTPException(
                status_code=409,
                detail="Another pod is currently syncing assets. Try again later."
            )
        return result
    
    # Legacy behavior without lock
    provider = get_provider()

    try:
        # Get supported assets from Fireblocks
        fb_assets = await provider.get_supported_assets()
        log.info(f"Fetched {len(fb_assets)} assets from Fireblocks")

        created = 0
        updated = 0
        skipped = 0
        created_list = []

        for fb_asset in fb_assets:
            asset_id = fb_asset.get("id", "")

            # Auto-parse the asset ID
            mapping = parse_fireblocks_asset(asset_id, fb_asset)

            if not mapping:
                skipped += 1
                continue

            # Check if asset already exists for fireblocks provider
            stmt = select(AssetModel).where(
                AssetModel.provider == "fireblocks",
                AssetModel.asset == asset_id,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # Update existing
                existing.blockchain = mapping["blockchain"]
                existing.symbol = mapping["currency"]
                existing.network = mapping["network"]
                existing.decimals = mapping["decimals"]
                existing.is_active = True
                updated += 1
            else:
                # Create new
                asset = AssetModel(
                    id=uuid4(),
                    asset=asset_id,
                    provider="fireblocks",
                    symbol=mapping["currency"],
                    display_name=mapping["currency"],
                    blockchain=mapping["blockchain"],
                    network=mapping["network"],
                    decimals=mapping["decimals"],
                    is_active=True,
                )
                db.add(asset)
                created += 1
                created_list.append(
                    {
                        "asset": asset_id,
                        "blockchain": mapping["blockchain"],
                        "currency": mapping["currency"],
                        "network": mapping["network"],
                    }
                )

        await db.commit()

        return {
            "status": "success",
            "total_fireblocks": len(fb_assets),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "message": f"Synced {created + updated} assets from Fireblocks",
            "created_assets": created_list[:20],  # Show first 20
        }

    except Exception as e:
        log.error(f"Error syncing assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/assets")
async def list_all_assets(db: AsyncSession = Depends(get_db)):
    """List all assets in database."""
    stmt = select(AssetModel).order_by(AssetModel.blockchain, AssetModel.currency)
    result = await db.execute(stmt)
    assets = result.scalars().all()

    return {
        "assets": [
            {
                "id": str(a.id),
                "asset": a.asset,
                "blockchain": a.blockchain,
                "currency": a.currency,
                "network": a.network,
                "decimals": a.decimals,
                "is_active": a.is_active,
                "is_testnet": a.is_testnet,
            }
            for a in assets
        ],
        "total": len(assets),
    }


@router.post("/add-asset")
async def add_asset_manually(
    asset: str,
    blockchain: str,
    currency: str,
    network: str,
    provider: str = "fireblocks",
    decimals: int = 18,
    testnet: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Manually add an asset to the database."""
    # Check if exists for this provider
    stmt = select(AssetModel).where(
        AssetModel.provider == provider,
        AssetModel.asset == asset,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail=f"Asset {asset} already exists for provider {provider}")

    new_asset = AssetModel(
        id=uuid4(),
        asset=asset,
        provider=provider,
        symbol=currency,
        display_name=currency,
        blockchain=blockchain,
        network=network,
        decimals=decimals,
        is_active=True,
        testnet=testnet,
    )
    db.add(new_asset)
    await db.commit()

    return {
        "status": "created",
        "asset": {
            "id": str(new_asset.id),
            "asset": new_asset.asset,
            "provider": new_asset.provider,
            "blockchain": new_asset.blockchain,
            "symbol": new_asset.symbol,
            "network": new_asset.network,
        },
    }
