"""Webhook schemas for Fireblocks."""

from pydantic import BaseModel, Field


class TransferPeerPathSchema(BaseModel):
    """Transaction source or destination."""

    type: str
    id: str | None = None
    name: str | None = None
    subType: str | None = None
    address: str | None = None


class AmountInfoSchema(BaseModel):
    """Transaction amount information."""

    amount: str | None = None
    requestedAmount: str | None = None
    netAmount: str | None = None
    amountUSD: str | None = None


class FeeInfoSchema(BaseModel):
    """Fee information."""

    networkFee: str | None = None
    serviceFee: str | None = None
    gasPrice: str | None = None


class BlockInfoSchema(BaseModel):
    """Block information."""

    blockHeight: str | None = None
    blockHash: str | None = None


class TransactionDetailsSchema(BaseModel):
    """Transaction details from Fireblocks webhook."""

    id: str
    externalTxId: str | None = None
    status: str
    subStatus: str | None = None
    txHash: str | None = None
    operation: str | None = None
    note: str | None = None
    assetId: str | None = None
    assetType: str | None = None
    source: TransferPeerPathSchema | None = None
    sourceAddress: str | None = None
    destination: TransferPeerPathSchema | None = None
    destinationAddress: str | None = None
    destinationAddressDescription: str | None = None
    destinationTag: str | None = None
    amountInfo: AmountInfoSchema | None = None
    feeInfo: FeeInfoSchema | None = None
    feeCurrency: str | None = None
    blockInfo: BlockInfoSchema | None = None
    signedBy: list[str] | None = None
    createdBy: str | None = None
    rejectedBy: str | None = None
    createdAt: int | None = None
    lastUpdated: int | None = None
    customerRefId: str | None = None
    numOfConfirmations: int | None = None
    exchangeTxId: str | None = None

    # Deprecated fields (for backward compatibility)
    amount: float | None = None
    netAmount: float | None = None
    amountUSD: float | None = None
    fee: float | None = None
    networkFee: float | None = None
    requestedAmount: float | None = None

    class Config:
        extra = "allow"


class FireblocksWebhookPayloadSchema(BaseModel):
    """Fireblocks webhook payload structure."""

    id: str = Field(..., description="Unique notification ID")
    resourceId: str | None = Field(None, description="Resource ID (e.g., txId)")
    workspaceId: str = Field(..., description="Fireblocks workspace ID")
    eventType: str = Field(..., description="Event type")
    createdAt: int = Field(..., description="Event creation timestamp")
    data: dict = Field(..., description="Event data")

    def get_transaction_details(self) -> TransactionDetailsSchema | None:
        """Get transaction details if this is a transaction event."""
        if self.eventType.startswith("transaction."):
            return TransactionDetailsSchema(**self.data)
        return None

    def is_incoming_deposit(self) -> bool:
        """
        Check if this is an incoming deposit event.

        Incoming deposit is a transaction where:
        - destination.type == VAULT_ACCOUNT
        - source.type != VAULT_ACCOUNT (external source)
        - operation == TRANSFER
        """
        if not self.eventType.startswith("transaction."):
            return False

        tx = self.get_transaction_details()
        if not tx:
            return False

        # Check if it's a TRANSFER operation
        if tx.operation != "TRANSFER":
            return False

        # Check if destination is our vault
        if not tx.destination or tx.destination.type != "VAULT_ACCOUNT":
            return False

        # Check if source is external (not our vault)
        if tx.source and tx.source.type == "VAULT_ACCOUNT":
            return False

        return True

    def is_completed_transaction(self) -> bool:
        """Check if transaction is completed successfully."""
        tx = self.get_transaction_details()
        if not tx:
            return False
        return tx.status == "COMPLETED"

    def is_internal_transfer(self) -> bool:
        """
        Check if this is an internal transfer between our vaults.

        Internal transfer is a transaction where:
        - source.type == VAULT_ACCOUNT (from our vault)
        - destination.type == VAULT_ACCOUNT (to our vault)
        - operation == TRANSFER
        
        Used to update balances when moving funds between HOT/WARM/COLD vaults.
        """
        if not self.eventType.startswith("transaction."):
            return False

        tx = self.get_transaction_details()
        if not tx:
            return False

        # Check if it's a TRANSFER operation
        if tx.operation != "TRANSFER":
            return False

        # Check if both source and destination are our vaults
        if not tx.source or tx.source.type != "VAULT_ACCOUNT":
            return False
        if not tx.destination or tx.destination.type != "VAULT_ACCOUNT":
            return False

        return True

    def is_outgoing_withdrawal(self) -> bool:
        """
        Check if this is an outgoing withdrawal/payout event.

        Outgoing withdrawal is a transaction where:
        - source.type == VAULT_ACCOUNT (from our vault)
        - destination.type != VAULT_ACCOUNT (external destination)
        - operation == TRANSFER
        """
        if not self.eventType.startswith("transaction."):
            return False

        tx = self.get_transaction_details()
        if not tx:
            return False

        # Check if it's a TRANSFER operation
        if tx.operation != "TRANSFER":
            return False

        # Check if source is our vault
        if not tx.source or tx.source.type != "VAULT_ACCOUNT":
            return False

        # Check if destination is external (not our vault)
        if tx.destination and tx.destination.type == "VAULT_ACCOUNT":
            return False

        return True

    def is_failed_or_rejected(self) -> bool:
        """Check if transaction failed or was rejected."""
        tx = self.get_transaction_details()
        if not tx:
            return False
        return tx.status in ("REJECTED", "BLOCKED", "FAILED", "CANCELLED", "TIMEOUT")


class WebhookProcessResultSchema(BaseModel):
    """Webhook processing result."""

    status: str = Field(
        ..., description="Processing status (created, updated, skipped, error)"
    )
    reason: str | None = Field(None, description="Reason for skip/error if any")
    transaction_id: str | None = Field(None, description="Transaction ID in our system")
    provider_tx_id: str | None = Field(
        None, description="Provider transaction ID (Fireblocks txId)"
    )
    amount: str | None = Field(None, description="Transaction amount")
    vault_id: str | None = Field(None, description="Vault ID")
    wallet_id: str | None = Field(None, description="Wallet ID")
    asset_id: str | None = Field(None, description="Asset ID")


class WebhookPayload(BaseModel):
    """Simple webhook payload for testing."""

    type: str = Field(..., description="Webhook event type")
    data: dict = Field(default_factory=dict, description="Event data")
