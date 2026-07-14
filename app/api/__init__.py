from fastapi import APIRouter

from app.api import vault, asset, transfer, whitelist, webhook, admin, treasury, wallet
from app.api.v1 import router as v1_router

router = APIRouter()

router.include_router(vault.router)
router.include_router(asset.router)
router.include_router(transfer.router)
router.include_router(whitelist.router)
router.include_router(webhook.router)
router.include_router(admin.router)
router.include_router(treasury.router, prefix="/treasury", tags=["treasury"])
router.include_router(wallet.router)

router.include_router(v1_router)

__all__ = ["router"]
