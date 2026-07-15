import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.api import router
from app.broker import (
    init_transfer_queues,
    close_transfer_queues,
    start_rejected_consumer,
    stop_rejected_consumer,
    start_approved_consumer,
    stop_approved_consumer,
)
from app.config import cfg, log
from app.services.custody.fireblocks import sync_fireblocks_assets
from app.services.http_client import http_client
from app.services.treasury_bootstrap import bootstrap_default_hot_wallet
from app.services.balance_sync import start_balance_sync_task, stop_balance_sync_task
from app.services.asset_sync import start_asset_sync_task, stop_asset_sync_task
from app.services.redis_client import init_redis, close_redis
from app.services.vault_client import vault_client
from app.storage import init_db, close_db, db_manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("🚀 Starting Custody Service...")

    if not os.getenv("MIGRATE_STAND"):
        if not cfg.app.API_KEY or not cfg.app.PRIVATE_KEY:
            raise RuntimeError(
                "Fireblocks credentials are required to start the service: set API_KEY and PRIVATE_KEY"
            )

    # includes Vault credentials refresh
    await init_db()

    # фоновое обновление Vault токена и DB credentials (dev/prod)
    await vault_client.start_background_refresh()
    await db_manager.start_background_refresh()

    http_client.initialize()

    # optional - for distributed locks
    await init_redis()

    await sync_fireblocks_assets()

    async with db_manager.get_db_local() as db:
        await bootstrap_default_hot_wallet(db)

    # Custody is source of truth
    await init_transfer_queues()
    log.info("📦 Transfer queues initialized")

    await start_rejected_consumer()
    await start_approved_consumer()
    log.info("📨 Transfer consumers started")

    await start_balance_sync_task()
    log.info("📊 Balance sync task started")

    await start_asset_sync_task()
    log.info("🔄 Asset sync task started")

    log.info("✅ Custody Service started")

    yield

    log.info("🛑 Shutting down Custody Service...")
    await vault_client.stop_background_refresh()
    await db_manager.stop_background_refresh()

    await stop_asset_sync_task()
    await stop_balance_sync_task()
    await stop_rejected_consumer()
    await stop_approved_consumer()
    await close_transfer_queues()
    await close_redis()
    await close_db()
    log.info("👋 Custody Service stopped")


app = FastAPI(
    title="Custody Service",
    description="Custody service for wallet and transaction management",
    version="1.0.0",
    lifespan=lifespan,
)


class HealthCheckFilter(logging.Filter):
    EXCLUDED_PATHS = ("/healthz", "/health", "/ready", "/live")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for path in self.EXCLUDED_PATHS:
            if f" {path} HTTP" in message:
                return False
        return True


logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

app.include_router(router)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "custody_v2"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    if not db_manager.initialized:
        raise HTTPException(status_code=503, detail="Database not initialized")

    return {"status": "ready"}
