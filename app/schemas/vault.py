from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums.status import VaultStatusEnum


class VaultTypeEnum(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    REGULAR = "regular"
    OPERATIONAL = "operational"


class VaultCreateRequest(BaseModel):
    name: str | None = Field(
        None, description="Vault name (auto-generated if not provided)"
    )
    assets: list[dict] | None = Field(
        None, description="List of assets [{currency, contract_address}, ...]"
    )
    auto_fuel: bool = Field(True, description="Enable auto fuel (Gas Station)")
    vault_type: VaultTypeEnum = Field(
        VaultTypeEnum.REGULAR,
        description="Тип vault'а: hot, warm, cold, regular, operational",
    )


class TreasuryAssetRequest(BaseModel):
    blockchain: str = Field(
        ..., description="Блокчейн (ETHEREUM, TRON, BSC, BITCOIN)"
    )
    contract_address: str | None = Field(
        None, description="Адрес контракта (None для нативных токенов: ETH, TRX, BTC)"
    )


class TreasuryVaultCreateRequest(BaseModel):
    name: str = Field(..., description="Vault name")
    vault_type: VaultTypeEnum = Field(..., description="Тип: hot, warm, cold, operational")
    assets: list[TreasuryAssetRequest] = Field(
        default_factory=list,
        description="Assets в формате [{currency, contract_address}, ...]",
    )

    # Treasury settings
    is_primary: bool = Field(False, description="Primary vault для данного типа")
    min_balance_usd: Decimal | None = Field(
        None, description="Мин. баланс USD для алертов"
    )
    max_balance_usd: Decimal | None = Field(
        None, description="Макс. баланс USD для rebalance"
    )
    target_balance_percent: int | None = Field(
        None, ge=0, le=100, description="Целевой % от общего баланса"
    )

    # Auto-refill settings (for HOT)
    auto_refill_enabled: bool = Field(False, description="Включить auto-refill")
    auto_refill_threshold_percent: int | None = Field(
        None,
        ge=0,
        le=100,
        description="Порог % для auto-refill (когда HOT < X%, пополнить из WARM)",
    )
    auto_refill_target_percent: int | None = Field(
        None, ge=0, le=100, description="Целевой % после auto-refill"
    )

    description: str | None = Field(None, description="Описание")


class TreasuryVaultUpdateRequest(BaseModel):
    min_balance_usd: Decimal | None = None
    max_balance_usd: Decimal | None = None
    target_balance_percent: int | None = None
    auto_refill_enabled: bool | None = None
    auto_refill_threshold_percent: int | None = None
    auto_refill_target_percent: int | None = None
    is_primary: bool | None = None
    description: str | None = None


class AssetInfo(BaseModel):
    blockchain: str
    currency: str
    network: str


class WalletInfo(BaseModel):
    wallet_id: UUID
    asset_id: UUID
    blockchain: str
    currency: str
    network: str
    address: str
    legacy_address: str | None = None
    tag: str | None = None


class WalletBalanceInfo(BaseModel):
    wallet_id: UUID
    asset_id: UUID
    blockchain: str
    currency: str
    network: str
    address: str
    balance: Decimal = Decimal("0")
    balance_usd: Decimal | None = None


class VaultCreateResponse(BaseModel):
    vault_id: UUID
    provider_vault_id: str
    name: str
    vault_type: str
    status: VaultStatusEnum
    wallets: list[WalletInfo] = Field(
        default_factory=list, description="Created wallets"
    )
    created_at: datetime


class VaultInfoResponse(BaseModel):
    vault_id: UUID
    provider_vault_id: str
    name: str
    vault_type: str
    status: VaultStatusEnum
    is_active: bool
    is_primary: bool = False
    wallets: list[WalletBalanceInfo] = Field(
        default_factory=list, description="List of wallets with balances"
    )
    created_at: datetime


class TreasuryVaultResponse(BaseModel):
    vault_id: UUID
    provider_vault_id: str
    name: str
    vault_type: str
    status: VaultStatusEnum
    is_active: bool
    is_primary: bool

    # Treasury settings
    min_balance_usd: Decimal | None = None
    max_balance_usd: Decimal | None = None
    target_balance_percent: int | None = None

    # Auto-refill
    auto_refill_enabled: bool = False
    auto_refill_threshold_percent: int | None = None
    auto_refill_target_percent: int | None = None

    # Balances
    total_balance_usd: Decimal = Decimal("0")
    wallets: list[WalletBalanceInfo] = Field(default_factory=list)

    # Health status
    health_status: str = "healthy"  # healthy, low, critical

    description: str | None = None
    created_at: datetime
    updated_at: datetime


class VaultListResponse(BaseModel):
    vaults: list[VaultInfoResponse]
    total: int


class TreasuryBalanceSummary(BaseModel):
    vault_type: str
    vault_count: int
    total_balance_usd: Decimal
    target_percent: int | None = None
    actual_percent: Decimal = Decimal("0")
    health_status: str = "healthy"


class TreasuryOverviewResponse(BaseModel):
    total_balance_usd: Decimal
    hot: TreasuryBalanceSummary | None = None
    warm: TreasuryBalanceSummary | None = None
    cold: TreasuryBalanceSummary | None = None

    # Health
    overall_health: str = "healthy"  # healthy, warning, critical
    alerts: list[str] = Field(default_factory=list)

    last_updated: datetime


class AssetBalanceResponse(BaseModel):
    blockchain: str
    currency: str
    network: str

    hot_balance: Decimal = Decimal("0")
    warm_balance: Decimal = Decimal("0")
    cold_balance: Decimal = Decimal("0")
    total_balance: Decimal = Decimal("0")

    hot_balance_usd: Decimal | None = None
    warm_balance_usd: Decimal | None = None
    cold_balance_usd: Decimal | None = None
    total_balance_usd: Decimal | None = None


class RebalanceRequest(BaseModel):
    source_vault_id: UUID = Field(..., description="Source vault ID")
    destination_vault_id: UUID = Field(..., description="Destination vault ID")
    asset_id: UUID = Field(..., description="Asset to transfer")
    amount: Decimal = Field(..., gt=0, description="Amount to transfer")
    note: str | None = Field(None, description="Transfer note")


class RebalanceResponse(BaseModel):
    transfer_id: UUID
    status: str  # pending_approval, approved, processing
    source_vault: str
    destination_vault: str
    amount: Decimal
    currency: str
    requires_approval: bool
    message: str
