from uuid import UUID

from pydantic import BaseModel, Field


class WhitelistAddRequest(BaseModel):
    vault_id: UUID
    asset_id: UUID
    address: str = Field(..., description="Address to add")
    description: str | None = Field(None, description="Optional description")


class WhitelistAddResponse(BaseModel):
    whitelist_id: str
    vault_id: UUID
    asset_id: UUID
    address: str
    status: str


class WhitelistCheckRequest(BaseModel):
    vault_id: UUID
    asset_id: UUID
    address: str


class WhitelistCheckResponse(BaseModel):
    address: str
    is_whitelisted: bool
    whitelist_id: str | None


class WhitelistListResponse(BaseModel):
    addresses: list[dict]
    total: int
