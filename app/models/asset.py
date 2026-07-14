"""Asset model - canonical list of supported assets.

This model is provider-agnostic. The Fireblocks asset ID is resolved dynamically
using contract_address (for tokens) or blockchain (for native coins).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, DateTime, Boolean, Integer, func, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .wallet import WalletModel
    from .transaction import TransactionModel


class AssetModel(Base):
    """
    Canonical asset model - provider-agnostic.
    
    Fireblocks asset ID is resolved at runtime via:
    - contract_address for tokens (ERC20, TRC20, etc.)
    - blockchain for native coins (ETH, BTC, TRX)
    """

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    
    # Provider-specific asset ID (e.g. USDT_TRX_TEST4 for Fireblocks)
    asset: Mapped[str] = mapped_column(
        String(100), index=True, 
        comment="Provider asset ID (USDT_TRX_TEST4, ETH_TEST5)"
    )
    
    # Provider name (fireblocks, self_custody, etc.)
    provider: Mapped[str] = mapped_column(
        String(50), default="fireblocks", server_default="fireblocks", index=True,
        comment="Custody provider (fireblocks, self_custody)"
    )
    
    # Asset identification
    symbol: Mapped[str] = mapped_column(
        String(20), index=True, comment="Short symbol (USDT, BTC, ETH)"
    )
    display_name: Mapped[str] = mapped_column(
        String(100), comment="Full display name (Tether USD, Bitcoin)"
    )
    
    # Blockchain information
    blockchain: Mapped[str] = mapped_column(
        String(50), index=True, comment="Blockchain (ETHEREUM, TRON, BITCOIN, BSC, SOLANA)"
    )
    
    # Token standard/protocol
    network: Mapped[str] = mapped_column(
        String(20), comment="Token standard (ERC20, TRC20, BEP20, SPL, NATIVE)"
    )
    
    # Contract address for tokens (NULL for native coins)
    contract_address: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True, 
        comment="Token contract address (null for native coins like ETH, BTC)"
    )
    
    # Network environment
    testnet: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True,
        comment="Testnet name (SEPOLIA, SHASTA, HOLESKY) or null for mainnet"
    )
    
    # Asset properties
    decimals: Mapped[int] = mapped_column(
        Integer, default=18, comment="Number of decimals"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="Is asset active for use"
    )
    is_native: Mapped[bool] = mapped_column(
        Boolean, server_default="false", comment="Is native/base coin (ETH, BTC, TRX)"
    )
    
    # Parent relationship (token -> native coin for gas)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Parent asset ID (native coin for tokens, for gas estimation)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="Created at"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        comment="Updated at"
    )

    # Relationships
    wallets: Mapped[list["WalletModel"]] = relationship(
        "WalletModel", back_populates="asset", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["TransactionModel"]] = relationship(
        "TransactionModel", back_populates="asset"
    )
    
    # Table constraints and indexes
    __table_args__ = (
        # Unique: provider + blockchain + contract_address + testnet
        # Allows same asset for different providers
        UniqueConstraint(
            "provider", "blockchain", "contract_address", "testnet",
            name="uq_asset_provider_blockchain_contract_testnet"
        ),
        # Unique asset ID per provider
        UniqueConstraint("provider", "asset", name="uq_asset_provider_asset"),
        Index("ix_assets_blockchain_testnet", "blockchain", "testnet"),
        Index("ix_assets_symbol_blockchain", "symbol", "blockchain"),
        Index("ix_assets_provider_blockchain", "provider", "blockchain"),
    )
    
    @property
    def is_testnet(self) -> bool:
        """Check if asset is on testnet."""
        return self.testnet is not None
    
    @property
    def is_mainnet(self) -> bool:
        """Check if asset is on mainnet."""
        return self.testnet is None
    
    @property
    def currency(self) -> str:
        """Alias for symbol (backward compatibility)."""
        return self.symbol

    def __repr__(self) -> str:
        env = f":{self.testnet}" if self.testnet else ""
        return f"<AssetModel({self.symbol} on {self.blockchain}{env})>"
