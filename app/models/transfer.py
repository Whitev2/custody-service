"""Transfer model - outgoing transactions (internal + external)."""

import uuid
from decimal import Decimal
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
    Boolean,
    Numeric,
    Text,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from app.enums.status import TransferStatus

if TYPE_CHECKING:
    from .vault import VaultModel
    from .wallet import WalletModel
    from .asset import AssetModel


class TransferModel(Base):
    """
    Outgoing transfer (internal + external) from HOT wallets.

    External transfers (is_internal=False):
    - Require approval from Workflow
    - Start with status=PENDING_APPROVAL
    - Balance reserved after approve

    Internal transfers (is_internal=True):
    - Whitelist only, no approval required
    - Start with status=PENDING
    - Balance reserved immediately
    """

    __tablename__ = "transfers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # External request ID for tracing (from Backend)
    request_id: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, comment="External request ID for tracing"
    )

    # Transfer type
    is_internal: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
        comment="True for whitelist transfers, False for external",
    )

    # Source wallet (NULL if pending_balance - not yet selected)
    vault_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vaults.id", ondelete="SET NULL"),
        index=True,
        comment="Source vault ID",
    )
    wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="SET NULL"),
        index=True,
        comment="Source wallet ID",
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        index=True,
        comment="Asset ID",
    )
    source_address: Mapped[str | None] = mapped_column(
        String(255), comment="Source HOT wallet address"
    )

    # For pending_balance queue - store original request params
    currency: Mapped[str] = mapped_column(
        String(20), index=True, comment="Asset symbol (ETH, USDT, TRX)"
    )
    contract_address: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
        comment="Token contract address (NULL for native tokens)",
    )
    blockchain: Mapped[str] = mapped_column(
        String(50), index=True, comment="Blockchain (ETHEREUM, TRON, BSC)"
    )
    network: Mapped[str | None] = mapped_column(
        String(20), comment="Network (ERC20, TRC20, BEP20, NATIVE, etc.)"
    )

    # Destination
    destination_address: Mapped[str] = mapped_column(
        String(255), index=True, comment="Destination wallet address"
    )
    destination_tag: Mapped[str | None] = mapped_column(
        String(255), comment="Destination tag/memo (XRP, XLM, etc.)"
    )
    to_vault_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vaults.id", ondelete="SET NULL"),
        comment="Destination vault for internal transfers",
    )

    # Amount
    amount: Mapped[Decimal] = mapped_column(Numeric(36, 18), comment="Transfer amount")
    amount_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), comment="Amount in USD"
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(30),
        default=TransferStatus.PENDING_APPROVAL.value,
        index=True,
        comment="Transfer status",
    )

    # Fireblocks transaction (filled after signing)
    provider_tx_id: Mapped[str | None] = mapped_column(
        String(255), index=True, comment="Fireblocks transaction ID"
    )
    tx_hash: Mapped[str | None] = mapped_column(
        String(255), index=True, comment="Blockchain transaction hash"
    )

    # Note
    note: Mapped[str | None] = mapped_column(String(500), comment="Optional note")

    # Timestamps
    reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="When balance was reserved"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="When transfer completed/rejected"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="Created at"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="Updated at",
    )

    # Error handling
    error_message: Mapped[str | None] = mapped_column(
        Text, comment="Error message if failed"
    )
    retry_count: Mapped[int] = mapped_column(
        default=0, comment="Number of balance reservation attempts"
    )

    # Relationships
    vault: Mapped["VaultModel | None"] = relationship(
        "VaultModel", foreign_keys=[vault_id]
    )
    wallet: Mapped["WalletModel | None"] = relationship("WalletModel")
    asset: Mapped["AssetModel | None"] = relationship("AssetModel")
    to_vault: Mapped["VaultModel | None"] = relationship(
        "VaultModel", foreign_keys=[to_vault_id]
    )

    __table_args__ = (
        Index("ix_transfers_status_created", "status", "created_at"),
        Index("ix_transfers_blockchain_contract", "blockchain", "contract_address"),
    )

    def __repr__(self) -> str:
        return f"<TransferModel(id={self.id}, request_id={self.request_id}, status={self.status})>"

    @property
    def is_pending(self) -> bool:
        """Is transfer still in progress."""
        return self.status in (
            TransferStatus.PENDING_APPROVAL.value,
            TransferStatus.PENDING_BALANCE.value,
            TransferStatus.PENDING.value,
            TransferStatus.SIGNING.value,
            TransferStatus.BROADCASTING.value,
        )

    @property
    def is_final(self) -> bool:
        """Is transfer in a final state."""
        return self.status in (
            TransferStatus.COMPLETED.value,
            TransferStatus.REJECTED.value,
            TransferStatus.FAILED.value,
            TransferStatus.CANCELLED.value,
        )

    @property
    def is_cancellable(self) -> bool:
        """Can this transfer be cancelled."""
        return self.status in (
            TransferStatus.PENDING_APPROVAL.value,
            TransferStatus.PENDING_BALANCE.value,
            TransferStatus.PENDING.value,
        )
