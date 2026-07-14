"""API v1 package - Asset management API."""

from fastapi import APIRouter

from app.api.v1 import assets

# Create v1 router
router = APIRouter(prefix="/v1")

# Include asset routes
router.include_router(assets.router)

__all__ = ["router"]
