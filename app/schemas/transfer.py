"""Transfer schemas for API."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Request Schemas
# ============================================================================


class ExternalTransferRequest(BaseModel):
    """
    External transfer request (requires Workflow approval).

    Used for payouts to external addresses.
    Starts with status=PENDING_APPROVAL, balance reserved after approve.
    """

    request_id: str = Field(..., description="Unique request ID for tracing")
    blockchain: str = Field(..., description="Blockchain (ethereum, tron, bsc)")
    asset: str = Field(..., description="Asset symbol (ETH, USDT, TRX)")
    to_address: str = Field(..., description="Destination address")
    amount: str = Field(..., description="Amount to transfer")
    contract_address: str | None = Field(
        ..., description="Token contract address (null for native)"
    )
    amount_usd: Decimal = Field(..., description="Amount in USD")
    destination_tag: str | None = Field(None, description="Memo/tag for XRP, XLM")
    note: str | None = Field(None, description="Optional note")


class InternalTransferRequest(BaseModel):
    """
    Internal transfer request (whitelist only, no approval required).

    Used for transfers between vaults or to whitelisted addresses.
    Balance reserved immediately, sent to Fireblocks right away.
    """

    request_id: str = Field(..., description="Unique request ID for tracing")
    blockchain: str = Field(..., description="Blockchain (ethereum, tron, bsc)")
    asset: str = Field(..., description="Asset symbol (ETH, USDT, TRX)")
    from_vault_id: UUID = Field(..., description="Source vault ID")
    to_vault_id: UUID | None = Field(None, description="Destination vault ID")
    to_address: str | None = Field(None, description="Destination address (whitelist)")
    amount: str = Field(..., description="Amount to transfer")
    contract_address: str | None = Field(
        ..., description="Token contract address (null for native)"
    )
    amount_usd: Decimal = Field(..., description="Amount in USD")
    destination_tag: str | None = Field(None, description="Memo/tag for XRP, XLM")
    note: str | None = Field(None, description="Optional note")


class RejectRequest(BaseModel):
    """Request to reject a transfer."""

    reason: str = Field(
        ..., description="Rejection reason", min_length=3, max_length=500
    )


class CompleteRequest(BaseModel):
    """Request to complete a transfer (after blockchain confirmation)."""

    tx_hash: str | None = Field(None, description="Blockchain transaction hash")


class SigningRequest(BaseModel):
    """Request to update transfer to signing status."""

    provider_tx_id: str = Field(..., description="Fireblocks transaction ID")


class CancelRequest(BaseModel):
    """Request to cancel a transfer."""

    reason: str = Field(
        ..., description="Cancellation reason", min_length=3, max_length=500
    )


# ============================================================================
# Response Schemas
# ============================================================================


class TransferResponse(BaseModel):
    """Response for both internal and external transfers."""
    model_config = ConfigDict(from_attributes=True)

    transfer_id: UUID = Field(..., description="Transfer ID")
    request_id: str = Field(..., description="Request ID for tracing")
    status: str = Field(..., description="Transfer status")
    is_internal: bool = Field(..., description="Is internal (whitelist) transfer")

    # Source
    source_vault_id: str | None = Field(None, description="Source vault ID")
    source_address: str | None = Field(None, description="Source wallet address")

    # Destination
    destination_address: str = Field(..., description="Destination address")
    destination_tag: str | None = Field(None, description="Memo/tag")
    to_vault_id: str | None = Field(None, description="Destination vault ID (internal)")

    # Amount & Asset
    amount: str = Field(..., description="Transfer amount")
    amount_usd: Decimal = Field(..., description="Amount in USD")
    asset: str = Field(..., description="Asset symbol (ETH, USDT)")
    blockchain: str = Field(..., description="Blockchain")
    contract_address: str | None = Field(None, description="Token contract address")

    # Transaction info
    provider_tx_id: str | None = Field(None, description="Fireblocks transaction ID")
    tx_hash: str | None = Field(None, description="Blockchain transaction hash")

    # Error
    error_message: str | None = Field(None, description="Error message if failed")

    # Timestamps
    created_at: datetime = Field(..., description="Created at")


class ApproveResponse(BaseModel):
    """Response after approve call."""

    request_id: str = Field(..., description="Request ID")
    status: str = Field(
        ..., description="New status: 'pending' (ready) or 'pending_balance' (queued)"
    )
    source_vault_id: str | None = Field(None, description="Selected HOT vault ID")
    source_address: str | None = Field(None, description="Selected HOT wallet address")
    message: str = Field(..., description="Status message")


class QueueStatsResponse(BaseModel):
    """Pending balance queue statistics."""

    pending_count: int = Field(
        ..., description="Number of transfers waiting for balance"
    )
    total_amount: float = Field(..., description="Total amount waiting")
    oldest_created_at: str | None = Field(
        None, description="Oldest transfer creation time"
    )


# ============================================================================
# Legacy compatibility - keeping old response format
# ============================================================================


class LegacyTransferResponse(BaseModel):
    """Legacy transfer response for backward compatibility."""

    transfer_id: UUID
    provider_tx_id: str
    from_vault_id: UUID
    to_vault_id: UUID | None
    to_address: str | None
    asset_id: UUID
    amount: str
    status: str
    is_internal: bool
    created_at: datetime
