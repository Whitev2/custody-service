# периодический рефреш кэша Fireblocks assets

import asyncio
from datetime import datetime, timezone

from app.config import log, cfg
from app.services.redis_client import DistributedLock


ASSET_SYNC_INTERVAL = 600

ASSET_SYNC_LOCK_NAME = "custody:asset-sync"
ASSET_SYNC_LOCK_TTL = 300  # 5 minutes - auto-release if pod crashes

_last_sync_time: datetime | None = None
_sync_task: asyncio.Task | None = None


async def start_asset_sync_task() -> None:
    global _sync_task
    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(_asset_sync_loop())
        log.info(f"🔄 Asset cache refresh task started (interval: {ASSET_SYNC_INTERVAL}s)")


async def stop_asset_sync_task() -> None:
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        log.info("🔄 Asset cache refresh task stopped")


async def _asset_sync_loop() -> None:
    # разброс старта по подам
    initial_delay = hash(cfg.app.STAND) % 60
    await asyncio.sleep(initial_delay)

    while True:
        try:
            await asyncio.sleep(ASSET_SYNC_INTERVAL)
            await refresh_asset_cache_with_lock()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"❌ Asset cache refresh loop error: {e}", exc_info=True)
            await asyncio.sleep(60)


async def refresh_asset_cache_with_lock() -> dict | None:
    # под distributed lock - только один под рефрешит
    global _last_sync_time

    async with DistributedLock(ASSET_SYNC_LOCK_NAME, ttl=ASSET_SYNC_LOCK_TTL) as acquired:
        if not acquired:
            log.debug("🔒 Asset cache refresh skipped - another pod is running")
            return {"status": "skipped", "reason": "lock_not_acquired"}

        try:
            log.info("🔄 Refreshing Fireblocks asset cache...")

            from app.services.custody.factory import get_provider
            provider = get_provider()

            if hasattr(provider, '_asset_cache'):
                provider._asset_cache = None

            assets = await provider.get_supported_assets()

            _last_sync_time = datetime.now(timezone.utc)
            
            log.info(f"✅ Asset cache refreshed: {len(assets)} assets at {_last_sync_time.isoformat()}")
            
            return {
                "status": "completed",
                "timestamp": _last_sync_time.isoformat(),
                "assets_count": len(assets),
            }
            
        except Exception as e:
            log.error(f"❌ Asset cache refresh failed: {e}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
            }


async def get_asset_sync_status() -> dict:
    return {
        "last_sync": _last_sync_time.isoformat() if _last_sync_time else None,
        "sync_interval_seconds": ASSET_SYNC_INTERVAL,
        "is_running": _sync_task is not None and not _sync_task.done(),
        "lock_name": ASSET_SYNC_LOCK_NAME,
        "lock_ttl_seconds": ASSET_SYNC_LOCK_TTL,
    }


async def force_sync_assets() -> dict:
    # ручной рефреш для admin API, тоже под lock
    result = await refresh_asset_cache_with_lock()
    return result or {"status": "skipped", "reason": "lock_not_acquired"}
