"""Treasury API - HOT/WARM/COLD wallet management."""

from decimal import Decimal
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import log, cfg
from app.models import VaultModel, WalletModel, AssetModel
from app.enums.types import VaultTypeEnum
from app.enums.status import VaultStatusEnum
from app.schemas.vault import (
    TreasuryAssetRequest,
    TreasuryVaultCreateRequest,
    TreasuryVaultUpdateRequest,
    TreasuryVaultResponse,
    TreasuryOverviewResponse,
    TreasuryBalanceSummary,
    AssetBalanceResponse,
    WalletBalanceInfo,
    RebalanceRequest,
    RebalanceResponse,
)
from app.storage import get_db
from app.services.custody import get_provider
from app.dao.asset import activate_asset_for_vault


router = APIRouter()


@router.post("/vaults", response_model=TreasuryVaultResponse)
async def create_treasury_vault(
    request: TreasuryVaultCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # assets: [{blockchain, contract_address}, ...], для нативных contract_address=null
    if request.vault_type not in (
        VaultTypeEnum.HOT,
        VaultTypeEnum.WARM,
        VaultTypeEnum.COLD,
        VaultTypeEnum.OPERATIONAL,
    ):
        raise HTTPException(
            status_code=400, detail="Treasury vaults must be HOT, WARM, COLD or OPERATIONAL type"
        )

    # если ставим primary - снимаем старый primary этого типа
    if request.is_primary:
        stmt = select(VaultModel).where(
            VaultModel.vault_type == request.vault_type.value,
            VaultModel.is_primary,
            VaultModel.is_active,
        )
        result = await db.execute(stmt)
        existing_primary = result.scalar_one_or_none()
        if existing_primary:
            existing_primary.is_primary = False

    provider = get_provider()
    is_testnet = cfg.app.is_testnet

    try:
        fb_vault = await provider.create_vault(request.name, auto_fuel=True)

        vault = VaultModel(
            provider_vault_id=fb_vault["id"],
            name=request.name,
            vault_type=request.vault_type.value,
            is_primary=request.is_primary,
            min_balance_usd=request.min_balance_usd,
            max_balance_usd=request.max_balance_usd,
            target_balance_percent=request.target_balance_percent,
            auto_refill_enabled=request.auto_refill_enabled,
            auto_refill_threshold_percent=request.auto_refill_threshold_percent,
            auto_refill_target_percent=request.auto_refill_target_percent,
            description=request.description,
            status=VaultStatusEnum.AVAILABLE.value,
            is_active=True,
        )
        db.add(vault)
        await db.flush()

        wallets = []
        for asset_req in request.assets:
            try:
                if asset_req.contract_address:
                    stmt = select(AssetModel).where(
                        AssetModel.blockchain == asset_req.blockchain.upper(),
                        AssetModel.contract_address == asset_req.contract_address,
                        AssetModel.is_active.is_(True),
                    )
                else:
                    stmt = select(AssetModel).where(
                        AssetModel.blockchain == asset_req.blockchain.upper(),
                        AssetModel.contract_address.is_(None),
                        AssetModel.is_active.is_(True),
                    )

                if is_testnet:
                    stmt = stmt.where(AssetModel.testnet.isnot(None))
                else:
                    stmt = stmt.where(AssetModel.testnet.is_(None))

                result = await db.execute(stmt)
                asset_model = result.scalar_one_or_none()

                if not asset_model:
                    log.warning(
                        f"Asset not found: blockchain={asset_req.blockchain}, "
                        f"contract={asset_req.contract_address}, is_testnet={is_testnet}"
                    )
                    continue

                asset_data = {
                    "blockchain": asset_model.blockchain,
                    "currency": asset_model.currency,
                    "network": asset_model.network,
                    "is_testnet": is_testnet,
                }

                wallet = await activate_asset_for_vault(db, vault, asset_data)
                wallets.append(
                    WalletBalanceInfo(
                        wallet_id=wallet.id,
                        asset_id=wallet.asset_id,
                        blockchain=wallet.asset.blockchain,
                        currency=wallet.asset.currency,
                        network=wallet.asset.network,
                        address=wallet.address,
                        balance=Decimal("0"),
                    )
                )
            except Exception as e:
                log.warning(
                    f"Failed to activate asset blockchain={asset_req.blockchain}, "
                    f"contract={asset_req.contract_address}: {e}"
                )

        await db.commit()

        return TreasuryVaultResponse(
            vault_id=vault.id,
            provider_vault_id=vault.provider_vault_id,
            name=vault.name,
            vault_type=vault.vault_type,
            status=vault.status,
            is_active=vault.is_active,
            is_primary=vault.is_primary,
            min_balance_usd=vault.min_balance_usd,
            max_balance_usd=vault.max_balance_usd,
            target_balance_percent=vault.target_balance_percent,
            auto_refill_enabled=vault.auto_refill_enabled,
            auto_refill_threshold_percent=vault.auto_refill_threshold_percent,
            auto_refill_target_percent=vault.auto_refill_target_percent,
            total_balance_usd=Decimal("0"),
            wallets=wallets,
            health_status="healthy",
            description=vault.description,
            created_at=vault.created_at,
            updated_at=vault.updated_at,
        )

    except Exception as e:
        log.error(f"Error creating treasury vault: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vaults", response_model=list[TreasuryVaultResponse])
async def list_treasury_vaults(
    db: Annotated[AsyncSession, Depends(get_db)],
    vault_type: VaultTypeEnum | None = Query(None, description="Filter by vault type"),
    include_inactive: bool = Query(False, description="Include inactive vaults"),
):
    """List all treasury vaults (HOT/WARM/COLD/OPERATIONAL)."""

    stmt = (
        select(VaultModel)
        .where(
            VaultModel.vault_type.in_(
                [
                    VaultTypeEnum.HOT.value,
                    VaultTypeEnum.WARM.value,
                    VaultTypeEnum.COLD.value,
                    VaultTypeEnum.OPERATIONAL.value,
                ]
            )
        )
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
    )

    if vault_type:
        stmt = stmt.where(VaultModel.vault_type == vault_type.value)

    if not include_inactive:
        stmt = stmt.where(VaultModel.is_active)

    stmt = stmt.order_by(VaultModel.vault_type, VaultModel.created_at)

    result = await db.execute(stmt)
    vaults = result.scalars().all()

    responses = []
    for vault in vaults:
        wallets = [
            WalletBalanceInfo(
                wallet_id=w.id,
                asset_id=w.asset_id,
                blockchain=w.asset.blockchain,
                currency=w.asset.currency,
                network=w.asset.network,
                address=w.address,
                balance=Decimal(w.balance or "0"),
            )
            for w in vault.wallets
        ]

        total_balance = sum(Decimal(w.balance or "0") for w in vault.wallets)

        responses.append(
            TreasuryVaultResponse(
                vault_id=vault.id,
                provider_vault_id=vault.provider_vault_id,
                name=vault.name,
                vault_type=vault.vault_type,
                status=vault.status,
                is_active=vault.is_active,
                is_primary=vault.is_primary,
                min_balance_usd=vault.min_balance_usd,
                max_balance_usd=vault.max_balance_usd,
                target_balance_percent=vault.target_balance_percent,
                auto_refill_enabled=vault.auto_refill_enabled,
                auto_refill_threshold_percent=vault.auto_refill_threshold_percent,
                auto_refill_target_percent=vault.auto_refill_target_percent,
                total_balance_usd=total_balance,  # Simplified, should convert to USD
                wallets=wallets,
                health_status=_get_vault_health(vault, total_balance),
                description=vault.description,
                created_at=vault.created_at,
                updated_at=vault.updated_at,
            )
        )

    return responses


@router.get("/vaults/{vault_id}", response_model=TreasuryVaultResponse)
async def get_treasury_vault(
    vault_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = (
        select(VaultModel)
        .where(VaultModel.id == vault_id)
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
    )

    result = await db.execute(stmt)
    vault = result.scalar_one_or_none()

    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    if vault.vault_type not in (
        VaultTypeEnum.HOT.value,
        VaultTypeEnum.WARM.value,
        VaultTypeEnum.COLD.value,
        VaultTypeEnum.OPERATIONAL.value,
    ):
        raise HTTPException(status_code=400, detail="Not a treasury vault")

    wallets = [
        WalletBalanceInfo(
            wallet_id=w.id,
            asset_id=w.asset_id,
            blockchain=w.asset.blockchain,
            currency=w.asset.currency,
            network=w.asset.network,
            address=w.address,
            balance=Decimal(w.balance or "0"),
        )
        for w in vault.wallets
    ]

    total_balance = sum(Decimal(w.balance or "0") for w in vault.wallets)

    return TreasuryVaultResponse(
        vault_id=vault.id,
        provider_vault_id=vault.provider_vault_id,
        name=vault.name,
        vault_type=vault.vault_type,
        status=vault.status,
        is_active=vault.is_active,
        is_primary=vault.is_primary,
        min_balance_usd=vault.min_balance_usd,
        max_balance_usd=vault.max_balance_usd,
        target_balance_percent=vault.target_balance_percent,
        auto_refill_enabled=vault.auto_refill_enabled,
        auto_refill_threshold_percent=vault.auto_refill_threshold_percent,
        auto_refill_target_percent=vault.auto_refill_target_percent,
        total_balance_usd=total_balance,
        wallets=wallets,
        health_status=_get_vault_health(vault, total_balance),
        description=vault.description,
        created_at=vault.created_at,
        updated_at=vault.updated_at,
    )


@router.patch("/vaults/{vault_id}", response_model=TreasuryVaultResponse)
async def update_treasury_vault(
    vault_id: UUID,
    request: TreasuryVaultUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = select(VaultModel).where(VaultModel.id == vault_id)
    result = await db.execute(stmt)
    vault = result.scalar_one_or_none()

    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    if vault.vault_type not in (
        VaultTypeEnum.HOT.value,
        VaultTypeEnum.WARM.value,
        VaultTypeEnum.COLD.value,
        VaultTypeEnum.OPERATIONAL.value,
    ):
        raise HTTPException(status_code=400, detail="Not a treasury vault")

    if request.min_balance_usd is not None:
        vault.min_balance_usd = request.min_balance_usd
    if request.max_balance_usd is not None:
        vault.max_balance_usd = request.max_balance_usd
    if request.target_balance_percent is not None:
        vault.target_balance_percent = request.target_balance_percent
    if request.auto_refill_enabled is not None:
        vault.auto_refill_enabled = request.auto_refill_enabled
    if request.auto_refill_threshold_percent is not None:
        vault.auto_refill_threshold_percent = request.auto_refill_threshold_percent
    if request.auto_refill_target_percent is not None:
        vault.auto_refill_target_percent = request.auto_refill_target_percent
    if request.description is not None:
        vault.description = request.description

    if request.is_primary is not None and request.is_primary:
        # снимаем старый primary
        unset_stmt = select(VaultModel).where(
            VaultModel.vault_type == vault.vault_type,
            VaultModel.is_primary,
            VaultModel.id != vault_id,
        )
        unset_result = await db.execute(unset_stmt)
        for existing in unset_result.scalars():
            existing.is_primary = False
        vault.is_primary = True
    elif request.is_primary is not None:
        vault.is_primary = request.is_primary

    await db.commit()

    return await get_treasury_vault(vault_id, db)


@router.post("/vaults/{vault_id}/activate-asset", response_model=WalletBalanceInfo)
async def activate_asset_in_treasury_vault(
    vault_id: UUID,
    request: TreasuryAssetRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Активировать ассет (создать wallet) в treasury vault."""
    is_testnet = cfg.app.is_testnet

    stmt = (
        select(VaultModel)
        .where(VaultModel.id == vault_id)
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
    )
    result = await db.execute(stmt)
    vault = result.scalar_one_or_none()

    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")

    if vault.vault_type not in (
        VaultTypeEnum.HOT.value,
        VaultTypeEnum.WARM.value,
        VaultTypeEnum.COLD.value,
        VaultTypeEnum.OPERATIONAL.value,
    ):
        raise HTTPException(status_code=400, detail="Not a treasury vault")

    if request.contract_address:
        asset_stmt = select(AssetModel).where(
            AssetModel.blockchain == request.blockchain.upper(),
            AssetModel.contract_address == request.contract_address,
            AssetModel.is_active.is_(True),
        )
    else:
        asset_stmt = select(AssetModel).where(
            AssetModel.blockchain == request.blockchain.upper(),
            AssetModel.contract_address.is_(None),
            AssetModel.is_active.is_(True),
        )

    if is_testnet:
        asset_stmt = asset_stmt.where(AssetModel.testnet.isnot(None))
    else:
        asset_stmt = asset_stmt.where(AssetModel.testnet.is_(None))

    asset_result = await db.execute(asset_stmt)
    asset_model = asset_result.scalar_one_or_none()

    if not asset_model:
        raise HTTPException(
            status_code=404,
            detail=f"Asset not found: blockchain={request.blockchain}, contract={request.contract_address}"
        )

    # уже активирован?
    for wallet in vault.wallets:
        if wallet.asset_id == asset_model.id:
            return WalletBalanceInfo(
                wallet_id=wallet.id,
                asset_id=wallet.asset_id,
                blockchain=wallet.asset.blockchain,
                currency=wallet.asset.currency,
                network=wallet.asset.network,
                address=wallet.address,
                balance=Decimal(wallet.balance or "0"),
            )

    try:
        asset_data = {
            "blockchain": asset_model.blockchain,
            "currency": asset_model.currency,
            "network": asset_model.network,
            "is_testnet": is_testnet,
        }
        wallet = await activate_asset_for_vault(db, vault, asset_data)
        await db.commit()

        return WalletBalanceInfo(
            wallet_id=wallet.id,
            asset_id=wallet.asset_id,
            blockchain=wallet.asset.blockchain,
            currency=wallet.asset.currency,
            network=wallet.asset.network,
            address=wallet.address,
            balance=Decimal("0"),
        )
    except Exception as e:
        log.error(f"Error activating asset in vault: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/overview", response_model=TreasuryOverviewResponse)
async def get_treasury_overview(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = (
        select(VaultModel)
        .where(
            VaultModel.is_active,
            VaultModel.vault_type.in_(
                [
                    VaultTypeEnum.HOT.value,
                    VaultTypeEnum.WARM.value,
                    VaultTypeEnum.COLD.value,
                ]
            ),
        )
        .options(selectinload(VaultModel.wallets))
    )

    result = await db.execute(stmt)
    vaults = result.scalars().all()

    type_balances = {
        VaultTypeEnum.HOT.value: {"count": 0, "balance": Decimal("0"), "target": None},
        VaultTypeEnum.WARM.value: {"count": 0, "balance": Decimal("0"), "target": None},
        VaultTypeEnum.COLD.value: {"count": 0, "balance": Decimal("0"), "target": None},
    }

    for vault in vaults:
        vt = vault.vault_type
        if vt in type_balances:
            type_balances[vt]["count"] += 1
            type_balances[vt]["balance"] += sum(
                Decimal(w.balance or "0") for w in vault.wallets
            )
            if vault.target_balance_percent:
                type_balances[vt]["target"] = vault.target_balance_percent

    total_balance = sum(tb["balance"] for tb in type_balances.values())

    alerts = []

    def make_summary(vt: str) -> TreasuryBalanceSummary | None:
        data = type_balances.get(vt)
        if not data or data["count"] == 0:
            return None

        actual_percent = (
            (data["balance"] / total_balance * 100)
            if total_balance > 0
            else Decimal("0")
        )

        health = "healthy"
        if data["target"]:
            diff = abs(float(actual_percent) - data["target"])
            if diff > 20:
                health = "critical"
                alerts.append(
                    f"{vt.upper()} balance is {actual_percent:.1f}% (target: {data['target']}%)"
                )
            elif diff > 10:
                health = "warning"

        return TreasuryBalanceSummary(
            vault_type=vt,
            vault_count=data["count"],
            total_balance_usd=data["balance"],
            target_percent=data["target"],
            actual_percent=actual_percent,
            health_status=health,
        )

    hot_summary = make_summary(VaultTypeEnum.HOT.value)
    warm_summary = make_summary(VaultTypeEnum.WARM.value)
    cold_summary = make_summary(VaultTypeEnum.COLD.value)

    overall_health = "healthy"
    if any(
        s and s.health_status == "critical"
        for s in [hot_summary, warm_summary, cold_summary]
    ):
        overall_health = "critical"
    elif any(
        s and s.health_status == "warning"
        for s in [hot_summary, warm_summary, cold_summary]
    ):
        overall_health = "warning"

    return TreasuryOverviewResponse(
        total_balance_usd=total_balance,
        hot=hot_summary,
        warm=warm_summary,
        cold=cold_summary,
        overall_health=overall_health,
        alerts=alerts,
        last_updated=datetime.now(timezone.utc),
    )


@router.get("/balances/by-asset", response_model=list[AssetBalanceResponse])
async def get_balances_by_asset(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Балансы по ассетам в разрезе HOT/WARM/COLD."""
    stmt = (
        select(VaultModel)
        .where(
            VaultModel.is_active,
            VaultModel.vault_type.in_(
                [
                    VaultTypeEnum.HOT.value,
                    VaultTypeEnum.WARM.value,
                    VaultTypeEnum.COLD.value,
                ]
            ),
        )
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
    )

    result = await db.execute(stmt)
    vaults = result.scalars().all()

    asset_balances: dict[str, dict] = {}

    for vault in vaults:
        for wallet in vault.wallets:
            if not wallet.asset:
                continue

            key = f"{wallet.asset.blockchain}/{wallet.asset.currency}/{wallet.asset.network}"

            if key not in asset_balances:
                asset_balances[key] = {
                    "blockchain": wallet.asset.blockchain,
                    "currency": wallet.asset.currency,
                    "network": wallet.asset.network,
                    "hot": Decimal("0"),
                    "warm": Decimal("0"),
                    "cold": Decimal("0"),
                }

            balance = Decimal(wallet.balance or "0")

            if vault.vault_type == VaultTypeEnum.HOT.value:
                asset_balances[key]["hot"] += balance
            elif vault.vault_type == VaultTypeEnum.WARM.value:
                asset_balances[key]["warm"] += balance
            elif vault.vault_type == VaultTypeEnum.COLD.value:
                asset_balances[key]["cold"] += balance

    return [
        AssetBalanceResponse(
            blockchain=data["blockchain"],
            currency=data["currency"],
            network=data["network"],
            hot_balance=data["hot"],
            warm_balance=data["warm"],
            cold_balance=data["cold"],
            total_balance=data["hot"] + data["warm"] + data["cold"],
        )
        for data in asset_balances.values()
    ]


@router.post("/rebalance", response_model=RebalanceResponse)
async def request_rebalance(
    request: RebalanceRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # из COLD - ручной approval, WARM->HOT можно авто по политике
    source_stmt = select(VaultModel).where(VaultModel.id == request.source_vault_id)
    source_result = await db.execute(source_stmt)
    source_vault = source_result.scalar_one_or_none()

    if not source_vault:
        raise HTTPException(status_code=404, detail="Source vault not found")

    dest_stmt = select(VaultModel).where(VaultModel.id == request.destination_vault_id)
    dest_result = await db.execute(dest_stmt)
    dest_vault = dest_result.scalar_one_or_none()

    if not dest_vault:
        raise HTTPException(status_code=404, detail="Destination vault not found")

    treasury_types = (
        VaultTypeEnum.HOT.value,
        VaultTypeEnum.WARM.value,
        VaultTypeEnum.COLD.value,
    )
    if (
        source_vault.vault_type not in treasury_types
        or dest_vault.vault_type not in treasury_types
    ):
        raise HTTPException(
            status_code=400, detail="Both vaults must be treasury vaults"
        )

    asset_stmt = select(AssetModel).where(AssetModel.id == request.asset_id)
    asset_result = await db.execute(asset_stmt)
    asset = asset_result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    requires_approval = source_vault.vault_type in (
        VaultTypeEnum.COLD.value,
        VaultTypeEnum.WARM.value,
    )

    # TODO: создать internal transfer request в workflow service
    transfer_id = uuid4()

    return RebalanceResponse(
        transfer_id=transfer_id,
        status="pending_approval" if requires_approval else "processing",
        source_vault=source_vault.name,
        destination_vault=dest_vault.name,
        amount=request.amount,
        currency=asset.currency,
        requires_approval=requires_approval,
        message=(
            "Transfer requires manual approval"
            if requires_approval
            else "Transfer is being processed"
        ),
    )


@router.get("/primary/{vault_type}", response_model=TreasuryVaultResponse)
async def get_primary_vault(
    vault_type: VaultTypeEnum,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if vault_type not in (VaultTypeEnum.HOT, VaultTypeEnum.WARM, VaultTypeEnum.COLD):
        raise HTTPException(status_code=400, detail="Invalid vault type for primary")

    stmt = (
        select(VaultModel)
        .where(
            VaultModel.vault_type == vault_type.value,
            VaultModel.is_primary,
            VaultModel.is_active,
        )
        .options(selectinload(VaultModel.wallets).selectinload(WalletModel.asset))
    )

    result = await db.execute(stmt)
    vault = result.scalar_one_or_none()

    if not vault:
        raise HTTPException(
            status_code=404,
            detail=f"No primary {vault_type.value.upper()} vault configured",
        )

    return await get_treasury_vault(vault.id, db)


@router.get("/sync/status", summary="Get balance sync status")
async def get_balance_sync_status() -> dict:
    from app.services.balance_sync import get_sync_status
    return await get_sync_status()


@router.post("/sync/run", summary="Run balance sync now")
async def run_balance_sync_now() -> dict:
    from app.services.balance_sync import sync_treasury_balances
    stats = await sync_treasury_balances()
    return {
        "status": "completed",
        **stats,
    }


@router.post("/sync-vaults", summary="Sync all vaults from Fireblocks")
async def sync_vaults_from_fireblocks(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Импорт всех vault'ов из Fireblocks в custody DB (создаёт недостающие)."""
    from app.services.custody import get_provider

    provider = get_provider()

    fb_vaults = await provider.get_vaults()
    log.info(f"Found {len(fb_vaults)} vaults in Fireblocks")
    
    synced = 0
    skipped = 0
    errors = []
    
    for fb_vault in fb_vaults:
        vault_id = fb_vault.get("id") or fb_vault.get("vaultAccountId")
        vault_name = fb_vault.get("name") or fb_vault.get("accountName", "")
        
        if not vault_id:
            errors.append(f"Vault without ID: {fb_vault}")
            continue

        stmt = select(VaultModel).where(VaultModel.provider_vault_id == str(vault_id))
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            skipped += 1
            continue

        # тип vault определяем по имени
        vault_type = VaultTypeEnum.REGULAR.value
        name_upper = vault_name.upper()
        if "HOT" in name_upper:
            vault_type = VaultTypeEnum.HOT.value
        elif "WARM" in name_upper:
            vault_type = VaultTypeEnum.WARM.value
        elif "COLD" in name_upper:
            vault_type = VaultTypeEnum.COLD.value
        elif "GAS" in name_upper or "FEE" in name_upper or "OPERATIONAL" in name_upper:
            vault_type = VaultTypeEnum.OPERATIONAL.value

        try:
            vault = VaultModel(
                provider_vault_id=str(vault_id),
                name=vault_name,
                vault_type=vault_type,
                status=VaultStatusEnum.AVAILABLE.value,
                is_active=True,
            )
            db.add(vault)
            await db.flush()
            synced += 1
            log.info(f"✅ Synced vault: {vault_name} (type={vault_type})")
        except Exception as e:
            errors.append(f"Failed to sync {vault_name}: {str(e)}")
            await db.rollback()
    
    await db.commit()
    
    return {
        "status": "completed",
        "fireblocks_vaults": len(fb_vaults),
        "synced": synced,
        "skipped": skipped,
        "errors": errors[:10] if errors else [],
    }


@router.get("/fireblocks/vaults", summary="Get raw vaults from Fireblocks")
async def get_fireblocks_vaults() -> list[dict]:
    """Vault'ы напрямую из Fireblocks (для дебага)."""
    from app.services.custody import get_provider

    provider = get_provider()
    fb_vaults = await provider.get_vaults()

    return [
        {
            "id": v.get("id") or v.get("vaultAccountId"),
            "name": v.get("name") or v.get("accountName", ""),
            "autoFuel": v.get("autoFuel", False),
        }
        for v in fb_vaults
    ]


@router.post("/wallets/{wallet_id}/reset-pending")
async def reset_wallet_pending(
    wallet_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reset pending_amount for a wallet (admin/debug endpoint)."""
    from sqlalchemy import update
    
    stmt = (
        update(WalletModel)
        .where(WalletModel.id == wallet_id)
        .values(pending_amount=Decimal("0"))
    )
    result = await db.execute(stmt)
    await db.commit()
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Wallet not found")
    
    return {"status": "ok", "wallet_id": str(wallet_id), "pending_amount": "0"}


def _get_vault_health(vault: VaultModel, total_balance: Decimal) -> str:
    if vault.min_balance_usd and total_balance < vault.min_balance_usd:
        return "critical"

    if vault.min_balance_usd and total_balance < vault.min_balance_usd * Decimal("1.5"):
        return "low"

    return "healthy"
