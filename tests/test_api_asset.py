"""
Tests for Asset API endpoints.
"""

import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from httpx import AsyncClient

from app.models import VaultModel, AssetModel, WalletModel, TransactionModel


class TestAssetAPI:
    """Tests for /asset endpoints."""

    @pytest.mark.asyncio
    async def test_create_asset_success(
        self, client: AsyncClient, test_vault: VaultModel, test_asset: AssetModel
    ):
        """Test successful asset creation in vault."""
        mock_provider = AsyncMock()
        mock_provider.activate_asset = AsyncMock(
            return_value={
                "address": "TNewAddress12345678901234567890123",
                "legacyAddress": None,
                "tag": None,
            }
        )
        mock_provider.add_whitelist_address = AsyncMock(return_value={"id": "wl_456"})

        with patch("app.dao.asset.get_provider", return_value=mock_provider):
            response = await client.post(
                "/asset/create",
                json={
                    "vault_id": str(test_vault.id),
                    "asset_id": str(test_asset.id),
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["vault_id"] == str(test_vault.id)
        assert data["asset_id"] == str(test_asset.id)
        assert "address" in data

    @pytest.mark.asyncio
    async def test_create_asset_vault_not_found(
        self, client: AsyncClient, test_asset: AssetModel
    ):
        """Test asset creation with non-existent vault."""
        fake_vault_id = uuid4()

        response = await client.post(
            "/asset/create",
            json={
                "vault_id": str(fake_vault_id),
                "asset_id": str(test_asset.id),
            },
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_asset_asset_not_found(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        """Test asset creation with non-existent asset."""
        fake_asset_id = uuid4()

        response = await client.post(
            "/asset/create",
            json={
                "vault_id": str(test_vault.id),
                "asset_id": str(fake_asset_id),
            },
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_asset_info_success(
        self, client: AsyncClient, test_wallet: WalletModel
    ):
        """Test getting asset info."""
        response = await client.get(
            f"/asset/{test_wallet.asset_id}/info",
            params={"vault_id": str(test_wallet.vault_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["asset_id"] == str(test_wallet.asset_id)
        assert data["vault_id"] == str(test_wallet.vault_id)
        assert data["address"] == test_wallet.address

    @pytest.mark.asyncio
    async def test_get_asset_info_not_found(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        """Test getting non-existent asset info."""
        fake_asset_id = uuid4()

        response = await client.get(
            f"/asset/{fake_asset_id}/info",
            params={"vault_id": str(test_vault.id)},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_asset_history_empty(
        self, client: AsyncClient, test_asset: AssetModel
    ):
        """Test getting asset history when empty."""
        response = await client.get(f"/asset/{test_asset.id}/history")

        assert response.status_code == 200
        data = response.json()
        assert "transactions" in data
        assert "total" in data
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_get_asset_history_with_data(
        self, client: AsyncClient, test_transaction: TransactionModel
    ):
        """Test getting asset history with transactions."""
        response = await client.get(f"/asset/{test_transaction.asset_id}/history")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["transactions"]) >= 1

    @pytest.mark.asyncio
    async def test_get_asset_history_pagination(
        self, client: AsyncClient, test_transaction: TransactionModel
    ):
        """Test getting asset history with pagination."""
        response = await client.get(
            f"/asset/{test_transaction.asset_id}/history",
            params={"skip": 0, "limit": 10},
        )

        assert response.status_code == 200
        data = response.json()
        assert "transactions" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_get_asset_addresses_success(
        self, client: AsyncClient, test_wallet: WalletModel
    ):
        """Test getting all addresses for asset."""
        response = await client.get(f"/asset/{test_wallet.asset_id}/addresses")

        assert response.status_code == 200
        data = response.json()
        assert "addresses" in data
        assert "total" in data
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_get_asset_addresses_empty(
        self, client: AsyncClient, test_asset: AssetModel
    ):
        """Test getting addresses when no wallets exist."""
        response = await client.get(f"/asset/{test_asset.id}/addresses")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
