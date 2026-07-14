"""Schemas for API v1 - Asset Admin API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ============= Asset Schemas =============

class AssetCreateRequest(BaseModel):
    """Request to create a new asset."""
    
    symbol: str = Field(..., max_length=20, description="Short symbol: USDT, BTC, ETH")
    display_name: str = Field(..., max_length=100, description="Full name: Tether USD, Bitcoin")
    blockchain: str = Field(..., max_length=50, description="Blockchain: ETHEREUM, TRON, BITCOIN")
    contract_address: str | None = Field(None, max_length=128, description="Token contract address (null for native)")
    network: str = Field(..., max_length=20, description="Token standard: ERC20, TRC20, SPL, NATIVE")
    decimals: int = Field(18, description="Number of decimals")
    testnet: str | None = Field(None, max_length=20, description="Testnet name: SEPOLIA, SHASTA")
    is_native: bool = Field(False, description="Is native/base asset")
    parent_id: UUID | None = Field(None, description="Parent asset ID (native coin for tokens)")


class AssetUpdateRequest(BaseModel):
    """Request to update an asset."""
    
    display_name: str | None = Field(None, max_length=100)
    decimals: int | None = None
    is_active: bool | None = None
    parent_id: UUID | None = None


class AssetResponse(BaseModel):
    """Asset response."""
    
    id: UUID
    symbol: str
    display_name: str
    blockchain: str
    contract_address: str | None
    network: str
    decimals: int
    testnet: str | None
    is_native: bool
    is_active: bool
    parent_id: UUID | None
    created_at: datetime


class AssetListResponse(BaseModel):
    """List of assets response."""
    
    assets: list[AssetResponse]
    total: int


# ============= Lookup Response =============

class AssetLookupRequest(BaseModel):
    """Request to lookup asset for provider resolution."""
    
    blockchain: str = Field(..., description="Blockchain: ETHEREUM, TRON")
    contract_address: str | None = Field(None, description="Token contract (null for native)")
    testnet: str | None = Field(None, description="Testnet name (null for mainnet)")


class FireblocksAssetResponse(BaseModel):
    """Response with resolved Fireblocks asset ID."""
    
    asset_id: UUID
    symbol: str
    blockchain: str
    fireblocks_asset_id: str = Field(..., description="Resolved Fireblocks asset ID: USDT_ETH, ETH")
