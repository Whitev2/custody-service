import uuid

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import String, DateTime, Boolean, Numeric, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums.types import VaultTypeEnum
from app.enums.status import VaultStatusEnum

from .base import Base

if TYPE_CHECKING:
    from .wallet import WalletModel
    from .transaction import TransactionModel


class VaultModel(Base):
    """
    Vault model with treasury management support.

    Supports different wallet types:
    - HOT: For instant user withdrawals (5-10% of funds)
    - WARM: Intermediate buffer, auto-refills HOT (20-30%)
    - COLD: Long-term storage, manual transfers only (60-70%)
    - MERCHANT_POOL: Deposit addresses for merchant invoices
    - USER: Personal user vaults
    - OPERATIONAL: Gas and fee payments
    """

    __tablename__ = "vaults"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_vault_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, comment="Fireblocks vault ID"
    )
    name: Mapped[str] = mapped_column(String(255), comment="Vault name")

    # Vault type for treasury management
    vault_type: Mapped[str] = mapped_column(
        String(50),
        default=VaultTypeEnum.REGULAR.value,
        index=True,
        comment="Тип vault: hot, warm, cold, regular, operational",
    )

    # Treasury settings
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Primary vault для данного типа (основной HOT/WARM/COLD)",
    )
    min_balance_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True, comment="Минимальный баланс USD (для алертов)"
    )
    max_balance_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Максимальный баланс USD (для auto-rebalance)",
    )
    target_balance_percent: Mapped[int | None] = mapped_column(
        nullable=True, comment="Целевой % от общего баланса (для rebalancing)"
    )

    # Auto-refill settings (for HOT wallet)
    auto_refill_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Включен ли auto-refill из WARM"
    )
    auto_refill_threshold_percent: Mapped[int | None] = mapped_column(
        nullable=True, comment="Порог % при котором запускается auto-refill"
    )
    auto_refill_target_percent: Mapped[int | None] = mapped_column(
        nullable=True, comment="Целевой % после auto-refill"
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(50),
        default=VaultStatusEnum.CREATING.value,
        comment="Status: creating, available, error, maintenance",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="Is vault active"
    )

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="Created at"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="Updated at",
    )

    # Description for admin
    description: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="Описание vault'а для админки"
    )

    # Relationships
    wallets: Mapped[list["WalletModel"]] = relationship(
        "WalletModel", back_populates="vault", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["TransactionModel"]] = relationship(
        "TransactionModel", back_populates="vault"
    )

    __table_args__ = (
        Index("ix_vaults_type_primary", "vault_type", "is_primary"),
        Index("ix_vaults_type_active", "vault_type", "is_active"),
    )

    @property
    def is_treasury_vault(self) -> bool:
        """Is this a treasury vault (HOT/WARM/COLD)."""
        return self.vault_type in (
            VaultTypeEnum.HOT.value,
            VaultTypeEnum.WARM.value,
            VaultTypeEnum.COLD.value,
        )

    @property
    def requires_approval(self) -> bool:
        """Does this vault require approval for outgoing transfers."""
        return self.vault_type in (VaultTypeEnum.WARM.value, VaultTypeEnum.COLD.value)

    def __repr__(self) -> str:
        return f"<VaultModel(id={self.id}, name={self.name}, type={self.vault_type}, status={self.status})>"
