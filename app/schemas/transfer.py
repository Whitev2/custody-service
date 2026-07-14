from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExternalTransferRequest(BaseModel):
    # payout наружу, требует апрув в Workflow. PENDING_APPROVAL -> резерв после approve

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
    # whitelist only, без апрува. Резерв сразу, шлём в Fireblocks сразу

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
    reason: str = Field(
        ..., description="Rejection reason", min_length=3, max_length=500
    )


class CompleteRequest(BaseModel):
    tx_hash: str | None = Field(None, description="Blockchain transaction hash")


class SigningRequest(BaseModel):
    provider_tx_id: str = Field(..., description="Fireblocks transaction ID")


class CancelRequest(BaseModel):
    reason: str = Field(
        ..., description="Cancellation reason", min_length=3, max_length=500
    )


class TransferResponse(BaseModel):
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
    request_id: str = Field(..., description="Request ID")
    status: str = Field(
        ..., description="New status: 'pending' (ready) or 'pending_balance' (queued)"
    )
    source_vault_id: str | None = Field(None, description="Selected HOT vault ID")
    source_address: str | None = Field(None, description="Selected HOT wallet address")
    message: str = Field(..., description="Status message")


class QueueStatsResponse(BaseModel):
    pending_count: int = Field(
        ..., description="Number of transfers waiting for balance"
    )
    total_amount: float = Field(..., description="Total amount waiting")
    oldest_created_at: str | None = Field(
        None, description="Oldest transfer creation time"
    )


class LegacyTransferResponse(BaseModel):
    # старый формат ответа, для обратной совместимости

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
