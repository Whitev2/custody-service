"""
Background task for periodic balance synchronization with Fireblocks.

Syncs treasury vault balances (HOT/WARM/COLD) every 5 minutes.
This ensures DB balances stay accurate even if webhooks are missed or delayed.

Uses canonical AssetModel with dynamic Fireblocks ID resolution.
"""
import asyncio
from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import log
from app.models import VaultModel, WalletModel, AssetModel
from app.storage import db_manager


# Sync interval in seconds (5 minutes)
SYNC_INTERVAL = 300

# Track last sync time to avoid running too frequently
_last_sync_time: datetime | None = None
_sync_task: asyncio.Task | None = None


async def start_balance_sync_task() -> None:
    """Start the background balance sync task."""
    global _sync_task
    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(_balance_sync_loop())
        log.info(f"📊 Balance sync task started (interval: {SYNC_INTERVAL}s)")


async def stop_balance_sync_task() -> None:
    """Stop the background balance sync task."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        log.info("📊 Balance sync task stopped")


async def _balance_sync_loop() -> None:
    """Main loop for periodic balance sync."""
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL)
            await sync_treasury_balances()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"❌ Balance sync error: {e}", exc_info=True)
            # Continue running even on errors
            await asyncio.sleep(60)


async def _build_fireblocks_id_map(wallets: list[WalletModel]) -> dict[str, WalletModel]:
    """
    Build a mapping of Fireblocks asset ID -> WalletModel.
    
    This resolves each wallet's canonical asset to its Fireblocks ID.
    """
    from app.services.custody.fireblocks.resolver import resolve_fireblocks_asset
    
    fb_to_wallet: dict[str, WalletModel] = {}
    
    for wallet in wallets:
        if not wallet.asset:
            continue
        
        try:
            fb_id = await resolve_fireblocks_asset(wallet.asset)
            if fb_id:
                fb_to_wallet[fb_id] = wallet
        except Exception as e:
            log.debug(f"Could not resolve Fireblocks ID for {wallet.asset.symbol}: {e}")
    
    return fb_to_wallet


async def sync_treasury_balances() -> dict:
    """
    Sync balances for all treasury vaults (HOT/WARM/COLD) from Fireblocks.
    
    Returns dict with sync statistics.
    """
    global _last_sync_time
    
    log.info("📊 Starting treasury balance sync...")
    
    stats = {
        "vaults_synced": 0,
        "wallets_synced": 0,
        "wallets_updated": 0,
        "errors": 0,
    }
    
    try:
        from app.services.custody import get_provider
        provider = get_provider()
        
        async with db_manager.get_db_local() as db:
            # Get all treasury vaults (HOT, WARM, COLD)
            stmt = (
                select(VaultModel)
                .where(VaultModel.vault_type.in_(["hot", "warm", "cold"]))
                .where(VaultModel.is_active.is_(True))
                .options(
                    selectinload(VaultModel.wallets).selectinload(WalletModel.asset)
                )
            )
            result = await db.execute(stmt)
            vaults = result.scalars().all()
            
            for vault in vaults:
                if not vault.provider_vault_id:
                    continue
                
                try:
                    # Fetch all balances for this vault from Fireblocks
                    vault_data = await provider._service.get_vault_balance(
                        vault.provider_vault_id
                    )
                    
                    if not vault_data or "assets" not in vault_data:
                        continue
                    
                    stats["vaults_synced"] += 1
                    
                    # Build map of Fireblocks balances by asset_id
                    # Use "total" not "available" - Fireblocks "available" excludes their pending txs
                    # Our pending_amount is separate and tracks our reservations
                    fb_balances = {
                        asset["id"]: Decimal(str(asset.get("total", 0)))
                        for asset in vault_data["assets"]
                        if asset.get("total")
                    }
                    
                    # Build mapping: Fireblocks ID -> Wallet
                    fb_to_wallet = await _build_fireblocks_id_map(vault.wallets)
                    
                    # Update each wallet balance
                    for fb_asset_id, balance in fb_balances.items():
                        wallet = fb_to_wallet.get(fb_asset_id)
                        if not wallet:
                            continue
                        
                        stats["wallets_synced"] += 1
                        
                        old_balance = wallet.balance or Decimal(0)
                        
                        # Update if different
                        if balance != old_balance:
                            wallet.balance = balance
                            stats["wallets_updated"] += 1
                            log.info(
                                f"📊 Balance updated: {vault.name}/{wallet.asset.symbol} "
                                f"{old_balance} -> {balance}"
                            )
                
                except Exception as e:
                    stats["errors"] += 1
                    log.error(
                        f"❌ Failed to sync vault {vault.name}: {e}"
                    )
            
            await db.commit()
        
            # Process pending_balance queue after sync (always check, not just on balance update)
            pending_processed = await _process_pending_queue_after_sync(db)
            if pending_processed > 0:
                stats["pending_processed"] = pending_processed
        
        _last_sync_time = datetime.now(timezone.utc)
        
        log.info(
            f"📊 Balance sync completed: "
            f"{stats['vaults_synced']} vaults, "
            f"{stats['wallets_synced']} wallets checked, "
            f"{stats['wallets_updated']} updated, "
            f"{stats.get('pending_processed', 0)} pending processed, "
            f"{stats['errors']} errors"
        )
        
    except Exception as e:
        log.error(f"❌ Balance sync failed: {e}", exc_info=True)
        stats["errors"] += 1
    
    return stats


async def _process_pending_queue_after_sync(db) -> int:
    """Process pending_balance transfers after balance sync."""
    from sqlalchemy import select
    from app.models.transfer import TransferModel
    from app.enums.status import TransferStatus
    from app.dao.transfer import process_pending_balance_transfer
    from app.broker.publisher import publish_transfer_created
    from app.services.custody.fireblocks.resolver import resolve_fireblocks_asset
    
    # Get pending_balance transfers
    stmt = (
        select(TransferModel)
        .where(TransferModel.status == TransferStatus.PENDING_BALANCE.value)
        .order_by(TransferModel.created_at.asc())
        .limit(20)
    )
    result = await db.execute(stmt)
    transfers = result.scalars().all()
    
    if not transfers:
        return 0
    
    processed = 0
    for transfer in transfers:
        try:
            success = await process_pending_balance_transfer(db, transfer)
            if success:
                # Get source vault provider_id for workflow
                source_vault_id = None
                fireblocks_asset_id = None
                asset_model = None
                
                if transfer.vault_id:
                    vault = await db.get(VaultModel, transfer.vault_id)
                    if vault:
                        source_vault_id = vault.provider_vault_id
                
                if transfer.asset_id:
                    asset_model = await db.get(AssetModel, transfer.asset_id)
                    if asset_model:
                        # Resolve Fireblocks ID dynamically
                        fireblocks_asset_id = await resolve_fireblocks_asset(asset_model)
                
                # Commit the transfer update before publishing
                await db.commit()
                
                await publish_transfer_created(
                    request_id=transfer.request_id,
                    destination_address=transfer.destination_address,
                    destination_tag=transfer.destination_tag,
                    amount=str(transfer.amount),
                    amount_usd=float(transfer.amount_usd) if transfer.amount_usd else 0,
                    asset=transfer.currency,
                    contract_address=transfer.contract_address,
                    blockchain=transfer.blockchain,
                    currency=asset_model.symbol if asset_model else transfer.currency,
                    network=asset_model.network if asset_model else (transfer.network or ""),
                    source_vault_id=source_vault_id,
                    source_address=transfer.source_address,
                    fireblocks_asset_id=fireblocks_asset_id,
                )
                processed += 1
                log.info(f"✅ Processed pending transfer after sync: {transfer.request_id}")
        except Exception as e:
            await db.rollback()
            log.error(f"❌ Failed to process pending transfer {transfer.id}: {e}")
    
    return processed


async def get_sync_status() -> dict:
    """Get current sync status."""
    return {
        "last_sync": _last_sync_time.isoformat() if _last_sync_time else None,
        "sync_interval_seconds": SYNC_INTERVAL,
        "is_running": _sync_task is not None and not _sync_task.done(),
    }
