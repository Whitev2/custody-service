from pydantic import BaseModel, Field


class TransferPeerPathSchema(BaseModel):
    type: str
    id: str | None = None
    name: str | None = None
    subType: str | None = None
    address: str | None = None


class AmountInfoSchema(BaseModel):
    amount: str | None = None
    requestedAmount: str | None = None
    netAmount: str | None = None
    amountUSD: str | None = None


class FeeInfoSchema(BaseModel):
    networkFee: str | None = None
    serviceFee: str | None = None
    gasPrice: str | None = None


class BlockInfoSchema(BaseModel):
    blockHeight: str | None = None
    blockHash: str | None = None


class TransactionDetailsSchema(BaseModel):
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
    id: str = Field(..., description="Unique notification ID")
    resourceId: str | None = Field(None, description="Resource ID (e.g., txId)")
    workspaceId: str = Field(..., description="Fireblocks workspace ID")
    eventType: str = Field(..., description="Event type")
    createdAt: int = Field(..., description="Event creation timestamp")
    data: dict = Field(..., description="Event data")

    def get_transaction_details(self) -> TransactionDetailsSchema | None:
        if self.eventType.startswith("transaction."):
            return TransactionDetailsSchema(**self.data)
        return None

    def is_incoming_deposit(self) -> bool:
        # входящий депозит: dest = наш vault, source внешний, TRANSFER
        if not self.eventType.startswith("transaction."):
            return False

        tx = self.get_transaction_details()
        if not tx:
            return False

        if tx.operation != "TRANSFER":
            return False

        if not tx.destination or tx.destination.type != "VAULT_ACCOUNT":
            return False

        if tx.source and tx.source.type == "VAULT_ACCOUNT":
            return False

        return True

    def is_completed_transaction(self) -> bool:
        tx = self.get_transaction_details()
        if not tx:
            return False
        return tx.status == "COMPLETED"

    def is_internal_transfer(self) -> bool:
        # перевод между нашими vault'ами (HOT/WARM/COLD) - обновляем балансы
        if not self.eventType.startswith("transaction."):
            return False

        tx = self.get_transaction_details()
        if not tx:
            return False

        if tx.operation != "TRANSFER":
            return False

        if not tx.source or tx.source.type != "VAULT_ACCOUNT":
            return False
        if not tx.destination or tx.destination.type != "VAULT_ACCOUNT":
            return False

        return True

    def is_outgoing_withdrawal(self) -> bool:
        # исходящий payout: source = наш vault, dest внешний, TRANSFER
        if not self.eventType.startswith("transaction."):
            return False

        tx = self.get_transaction_details()
        if not tx:
            return False

        if tx.operation != "TRANSFER":
            return False

        if not tx.source or tx.source.type != "VAULT_ACCOUNT":
            return False

        if tx.destination and tx.destination.type == "VAULT_ACCOUNT":
            return False

        return True

    def is_failed_or_rejected(self) -> bool:
        tx = self.get_transaction_details()
        if not tx:
            return False
        return tx.status in ("REJECTED", "BLOCKED", "FAILED", "CANCELLED", "TIMEOUT")


class WebhookProcessResultSchema(BaseModel):
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
    # простой payload для тестов
    type: str = Field(..., description="Webhook event type")
    data: dict = Field(default_factory=dict, description="Event data")
