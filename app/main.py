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
    """Application lifespan events."""
    # Startup
    log.info("🚀 Starting Custody Service...")

    if not os.getenv("MIGRATE_STAND"):
        if not cfg.app.API_KEY or not cfg.app.PRIVATE_KEY:
            raise RuntimeError(
                "Fireblocks credentials are required to start the service: set API_KEY and PRIVATE_KEY"
            )

    # Initialize database (includes Vault credentials refresh)
    await init_db()

    # Запуск фоновой задачи обновления Vault токена (для dev/prod)
    await vault_client.start_background_refresh()

    # Запуск фоновой задачи обновления DB credentials (для dev/prod)
    await db_manager.start_background_refresh()

    # Инициализация http клиента
    http_client.initialize()

    # Initialize Redis (optional - for distributed locks)
    await init_redis()

    # Auto-sync assets from Fireblocks
    await sync_fireblocks_assets()

    # Bootstrap default HOT wallet if not exists
    async with db_manager.get_db_local() as db:
        await bootstrap_default_hot_wallet(db)

    # Initialize all transfer.* queues (Custody is source of truth)
    await init_transfer_queues()
    log.info("📦 Transfer queues initialized")

    # Start RabbitMQ consumers
    await start_rejected_consumer()
    await start_approved_consumer()
    log.info("📨 Transfer consumers started")

    # Start background balance sync task (every 5 minutes)
    await start_balance_sync_task()
    log.info("📊 Balance sync task started")

    # Start background asset sync task (every 10 minutes)
    await start_asset_sync_task()
    log.info("🔄 Asset sync task started")

    log.info("✅ Custody Service started")

    yield

    # Shutdown
    log.info("🛑 Shutting down Custody Service...")
    # Остановка фоновых задач Vault и DB
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


# Create FastAPI app
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

# Include routers
app.include_router(router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "custody"}


@app.get("/healthz")
async def healthz():
    """Liveness probe endpoint - checks if app is alive."""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness probe endpoint - checks if app is ready for traffic."""

    # Check if database is initialized and connected
    if not db_manager.initialized:
        raise HTTPException(status_code=503, detail="Database not initialized")

    return {"status": "ready"}
