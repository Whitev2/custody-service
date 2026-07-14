"""
Pytest configuration and fixtures for custody_v2 tests.

Markers:
- @pytest.mark.integration: Tests that require real Fireblocks API
- @pytest.mark.slow: Tests that take longer to run

Run only unit tests: pytest tests/ -m "not integration"
Run only integration tests: pytest tests/ -m integration
Run all tests: pytest tests/
"""

import asyncio
import os
from decimal import Decimal
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models.base import Base
from app.models import VaultModel, AssetModel, WalletModel, TransactionModel
from app.storage.database import get_db


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require real Fireblocks API)",
    )
    config.addinivalue_line("markers", "slow: marks tests as slow running")


# Test database URL (SQLite in memory)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    # Enable foreign keys for SQLite
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    async_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(test_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test HTTP client with overridden dependencies."""

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# === Model Fixtures ===


@pytest_asyncio.fixture
async def test_asset(test_session: AsyncSession) -> AssetModel:
    """Create test asset."""
    asset = AssetModel(
        id=uuid4(),
        asset="USDT_TRX_TEST",
        currency="USDT",
        blockchain="TRON",
        network="TRC20",
        decimals=6,
        is_active=True,
        is_testnet=True,
    )
    test_session.add(asset)
    await test_session.commit()
    await test_session.refresh(asset)
    return asset


@pytest_asyncio.fixture
async def test_asset_eth(test_session: AsyncSession) -> AssetModel:
    """Create test ETH asset."""
    asset = AssetModel(
        id=uuid4(),
        asset="USDT_ERC20_TEST",
        currency="USDT",
        blockchain="ETHEREUM",
        network="ERC20",
        decimals=6,
        is_active=True,
        is_testnet=True,
    )
    test_session.add(asset)
    await test_session.commit()
    await test_session.refresh(asset)
    return asset


@pytest_asyncio.fixture
async def test_vault(test_session: AsyncSession) -> VaultModel:
    """Create test vault."""
    vault = VaultModel(
        id=uuid4(),
        provider_vault_id=f"fb_vault_{uuid4().hex[:8]}",
        name=f"TEST_VAULT_{uuid4().hex[:6]}",
        status="available",
        is_active=True,
    )
    test_session.add(vault)
    await test_session.commit()
    await test_session.refresh(vault)
    return vault


@pytest_asyncio.fixture
async def test_wallet(
    test_session: AsyncSession, test_vault: VaultModel, test_asset: AssetModel
) -> WalletModel:
    """Create test wallet."""
    wallet = WalletModel(
        id=uuid4(),
        vault_id=test_vault.id,
        asset_id=test_asset.id,
        address=f"T{uuid4().hex[:32]}",
        legacy_address=None,
        tag=None,
        balance=Decimal("100.0"),
    )
    test_session.add(wallet)
    await test_session.commit()
    await test_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def test_transaction(
    test_session: AsyncSession,
    test_vault: VaultModel,
    test_wallet: WalletModel,
    test_asset: AssetModel,
) -> TransactionModel:
    """Create test transaction."""
    tx = TransactionModel(
        id=uuid4(),
        provider_tx_id=f"fb_tx_{uuid4().hex[:8]}",
        tx_hash=f"0x{uuid4().hex}",
        vault_id=test_vault.id,
        wallet_id=test_wallet.id,
        asset_id=test_asset.id,
        amount=Decimal("50.0"),
        amount_usd=Decimal("50.0"),
        status="COMPLETED",
        num_confirmations=12,
        is_internal=False,
        source_address=f"T{uuid4().hex[:32]}",
        destination_address=test_wallet.address,
    )
    test_session.add(tx)
    await test_session.commit()
    await test_session.refresh(tx)
    return tx


# === Mock Provider Fixture ===


@pytest.fixture
def mock_provider(mocker):
    """Mock the custody provider."""
    mock = mocker.MagicMock()

    # Mock create_vault
    mock.create_vault = mocker.AsyncMock(
        return_value={
            "id": f"fb_vault_{uuid4().hex[:8]}",
            "name": "TEST_VAULT",
        }
    )

    # Mock activate_asset
    mock.activate_asset = mocker.AsyncMock(
        return_value={
            "address": f"T{uuid4().hex[:32]}",
            "legacyAddress": None,
            "tag": None,
        }
    )

    # Mock create_transaction
    mock.create_transaction = mocker.AsyncMock(
        return_value={
            "id": f"fb_tx_{uuid4().hex[:8]}",
            "txHash": None,
            "status": "SUBMITTED",
        }
    )

    # Mock whitelist operations
    mock.add_whitelist_address = mocker.AsyncMock(return_value={"id": "wl_123"})
    mock.get_whitelist_addresses = mocker.AsyncMock(return_value=[])
    mock.remove_whitelist_address = mocker.AsyncMock(return_value={})

    return mock


# === Integration Test Fixtures ===


@pytest.fixture(scope="session")
def fireblocks_credentials():
    """Check if Fireblocks credentials are available."""
    api_key = os.getenv("API_KEY")
    private_key_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "secrets", "fireblocks.key"
    )

    has_key_file = os.path.exists(private_key_path)

    if not api_key and not has_key_file:
        pytest.skip("Fireblocks credentials not configured")

    return {
        "api_key": api_key,
        "private_key_path": private_key_path if has_key_file else None,
    }


@pytest.fixture(scope="session")
def real_fireblocks_service(fireblocks_credentials):
    """Get real Fireblocks service for integration tests."""
    from app.services.custody import FireblocksService

    return FireblocksService()


@pytest_asyncio.fixture(scope="function")
async def integration_session():
    """
    Create database session for integration tests.
    Uses SQLite for simplicity but can be configured for PostgreSQL.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_maker() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def integration_client(integration_session: AsyncSession):
    """Create HTTP client for integration tests with real Fireblocks."""

    async def override_get_db():
        yield integration_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
