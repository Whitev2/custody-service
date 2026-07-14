"""Whitelist schemas for API."""

from uuid import UUID

from pydantic import BaseModel, Field


class WhitelistAddRequest(BaseModel):
    """Request to add address to whitelist."""

    vault_id: UUID
    asset_id: UUID
    address: str = Field(..., description="Address to add")
    description: str | None = Field(None, description="Optional description")


class WhitelistAddResponse(BaseModel):
    """Response after adding to whitelist."""

    whitelist_id: str
    vault_id: UUID
    asset_id: UUID
    address: str
    status: str


class WhitelistCheckRequest(BaseModel):
    """Request to check if address is whitelisted."""

    vault_id: UUID
    asset_id: UUID
    address: str


class WhitelistCheckResponse(BaseModel):
    """Response for whitelist check."""

    address: str
    is_whitelisted: bool
    whitelist_id: str | None


class WhitelistListResponse(BaseModel):
    """Whitelist addresses response."""

    addresses: list[dict]
    total: int
