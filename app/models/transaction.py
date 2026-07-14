import uuid
from decimal import Decimal
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
    Integer,
    Boolean,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .vault import VaultModel
    from .wallet import WalletModel
    from .asset import AssetModel


class TransactionModel(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str | None] = mapped_column(
        String(50), index=True, comment="External request ID for tracing (from externalTxId)"
    )
    provider_tx_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, comment="Fireblocks transaction ID"
    )
    tx_hash: Mapped[str | None] = mapped_column(
        String(255), index=True, comment="Transaction hash"
    )
    vault_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vaults.id", ondelete="SET NULL"),
        index=True,
        comment="Vault ID",
    )
    wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="SET NULL"),
        index=True,
        comment="Wallet ID",
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        index=True,
        comment="Asset ID",
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(36, 18), comment="Transaction amount"
    )
    amount_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(36, 18), comment="Amount in USD"
    )
    status: Mapped[str] = mapped_column(
        String(50), index=True, comment="Technical status from Fireblocks"
    )
    num_confirmations: Mapped[int | None] = mapped_column(
        Integer, comment="Number of confirmations"
    )
    is_internal: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Is internal transfer (whitelist)"
    )
    source_address: Mapped[str | None] = mapped_column(
        String(255), comment="Source address"
    )
    destination_address: Mapped[str | None] = mapped_column(
        String(255), comment="Destination address"
    )
    raw_webhook_data: Mapped[str | None] = mapped_column(
        Text, comment="Raw webhook data"
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

    vault: Mapped["VaultModel | None"] = relationship(
        "VaultModel", back_populates="transactions"
    )
    wallet: Mapped["WalletModel | None"] = relationship(
        "WalletModel", back_populates="transactions"
    )
    asset: Mapped["AssetModel | None"] = relationship(
        "AssetModel", back_populates="transactions"
    )

    def __repr__(self) -> str:
        return f"<TransactionModel(id={self.id}, provider_tx_id={self.provider_tx_id}, status={self.status})>"
