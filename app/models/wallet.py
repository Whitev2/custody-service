import uuid
from decimal import Decimal
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .vault import VaultModel
    from .asset import AssetModel
    from .transaction import TransactionModel


class WalletModel(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vaults.id", ondelete="CASCADE"),
        index=True,
        comment="Vault ID",
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        index=True,
        comment="Asset ID",
    )
    address: Mapped[str] = mapped_column(
        String(255), index=True, comment="Wallet address"
    )
    legacy_address: Mapped[str | None] = mapped_column(
        String(255), comment="Legacy address (for BTC)"
    )
    tag: Mapped[str | None] = mapped_column(
        String(255), comment="Tag/Memo (for XRP, XLM)"
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(36, 18), server_default="0", comment="Asset balance"
    )
    pending_amount: Mapped[Decimal] = mapped_column(
        Numeric(36, 18), server_default="0",
        comment="Reserved amount for pending payouts"
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

    vault: Mapped["VaultModel"] = relationship("VaultModel", back_populates="wallets")
    asset: Mapped["AssetModel"] = relationship("AssetModel", back_populates="wallets")
    transactions: Mapped[list["TransactionModel"]] = relationship(
        "TransactionModel", back_populates="wallet"
    )

    def __repr__(self) -> str:
        return f"<WalletModel(id={self.id}, vault_id={self.vault_id}, address={self.address})>"
    
    @property
    def available_balance(self) -> Decimal:
        return self.balance - self.pending_amount

    def has_sufficient_balance(self, amount: Decimal) -> bool:
        return self.available_balance >= amount
