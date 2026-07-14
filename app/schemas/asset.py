"""Asset schemas for API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AssetCreateRequest(BaseModel):
    """
    Request to create asset in vault.
    
    Поиск asset:
    1. По asset_id (если указан)
    2. По blockchain + contract_address
       - contract_address = "0x..." → токен
       - contract_address = null → нативная монета
    """

    vault_id: UUID
    asset_id: UUID | None = Field(None, description="Asset ID from assets table")
    blockchain: str | None = Field(None, description="Blockchain (ETHEREUM, TRON, etc)")
    contract_address: str | None = Field(None, description="Contract address (null for native coins)")


class AssetInfoResponse(BaseModel):
    """Asset information response."""

    asset_id: UUID
    wallet_id: UUID
    vault_id: UUID
    address: str
    legacy_address: str | None
    tag: str | None
    balance: str
    blockchain: str
    currency: str
    network: str
    created_at: datetime


class AssetHistoryResponse(BaseModel):
    """Asset transaction history response."""

    transactions: list[dict]
    total: int


class AssetAddressesResponse(BaseModel):
    """Asset addresses across all vaults."""

    addresses: list[dict]
    total: int
