"""
Tests for Whitelist API endpoints.
"""

import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from httpx import AsyncClient

from app.models import VaultModel, AssetModel


class TestWhitelistAPI:
    """Tests for /whitelist endpoints."""

    @pytest.mark.asyncio
    async def test_add_whitelist_address_success(
        self, client: AsyncClient, test_vault: VaultModel, test_asset: AssetModel
    ):
        """Test adding address to whitelist."""
        mock_provider = AsyncMock()
        mock_provider.add_whitelist_address = AsyncMock(
            return_value={
                "id": "wl_new_123",
                "address": "TNewWhitelistAddress123456789012",
            }
        )

        with patch("app.api.whitelist.get_provider", return_value=mock_provider):
            response = await client.post(
                "/whitelist/add",
                json={
                    "vault_id": str(test_vault.id),
                    "asset_id": str(test_asset.id),
                    "address": "TNewWhitelistAddress123456789012",
                    "description": "Test whitelist entry",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["address"] == "TNewWhitelistAddress123456789012"
        assert data["status"] == "added"

    @pytest.mark.asyncio
    async def test_add_whitelist_vault_not_found(
        self, client: AsyncClient, test_asset: AssetModel
    ):
        """Test adding to whitelist with non-existent vault."""
        fake_vault_id = uuid4()

        response = await client.post(
            "/whitelist/add",
            json={
                "vault_id": str(fake_vault_id),
                "asset_id": str(test_asset.id),
                "address": "TNewWhitelistAddress123456789012",
            },
        )

        assert response.status_code == 404
        assert "vault" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_add_whitelist_asset_not_found(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        """Test adding to whitelist with non-existent asset."""
        fake_asset_id = uuid4()

        response = await client.post(
            "/whitelist/add",
            json={
                "vault_id": str(test_vault.id),
                "asset_id": str(fake_asset_id),
                "address": "TNewWhitelistAddress123456789012",
            },
        )

        assert response.status_code == 404
        assert "asset" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_list_whitelist_success(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        """Test listing whitelist addresses."""
        mock_provider = AsyncMock()
        mock_provider.get_whitelist_addresses = AsyncMock(
            return_value=[
                {"id": "wl_1", "address": "TAddress1", "asset": "USDT_TRX"},
                {"id": "wl_2", "address": "TAddress2", "asset": "USDT_TRX"},
            ]
        )

        with patch("app.api.whitelist.get_provider", return_value=mock_provider):
            response = await client.get(
                "/whitelist/list",
                params={"vault_id": str(test_vault.id)},
            )

        assert response.status_code == 200
        data = response.json()
        assert "addresses" in data
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_list_whitelist_with_asset_filter(
        self, client: AsyncClient, test_vault: VaultModel, test_asset: AssetModel
    ):
        """Test listing whitelist addresses with asset filter."""
        mock_provider = AsyncMock()
        mock_provider.get_whitelist_addresses = AsyncMock(
            return_value=[
                {"id": "wl_1", "address": "TAddress1", "asset": "USDT_TRX"},
            ]
        )

        with patch("app.api.whitelist.get_provider", return_value=mock_provider):
            response = await client.get(
                "/whitelist/list",
                params={
                    "vault_id": str(test_vault.id),
                    "asset_id": str(test_asset.id),
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "addresses" in data

    @pytest.mark.asyncio
    async def test_list_whitelist_vault_not_found(self, client: AsyncClient):
        """Test listing whitelist for non-existent vault."""
        fake_vault_id = uuid4()

        response = await client.get(
            "/whitelist/list",
            params={"vault_id": str(fake_vault_id)},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_check_whitelist_address_found(
        self, client: AsyncClient, test_vault: VaultModel, test_asset: AssetModel
    ):
        """Test checking address in whitelist - found."""
        mock_provider = AsyncMock()
        mock_provider.get_whitelist_addresses = AsyncMock(
            return_value=[
                {"id": "wl_1", "address": "TWhitelistedAddress12345678901234"},
            ]
        )

        with patch("app.api.whitelist.get_provider", return_value=mock_provider):
            response = await client.post(
                "/whitelist/check",
                json={
                    "vault_id": str(test_vault.id),
                    "asset_id": str(test_asset.id),
                    "address": "TWhitelistedAddress12345678901234",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["is_whitelisted"] is True
        assert data["whitelist_id"] == "wl_1"

    @pytest.mark.asyncio
    async def test_check_whitelist_address_not_found(
        self, client: AsyncClient, test_vault: VaultModel, test_asset: AssetModel
    ):
        """Test checking address in whitelist - not found."""
        mock_provider = AsyncMock()
        mock_provider.get_whitelist_addresses = AsyncMock(return_value=[])

        with patch("app.api.whitelist.get_provider", return_value=mock_provider):
            response = await client.post(
                "/whitelist/check",
                json={
                    "vault_id": str(test_vault.id),
                    "asset_id": str(test_asset.id),
                    "address": "TNotInWhitelist12345678901234567",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["is_whitelisted"] is False
        assert data["whitelist_id"] is None

    @pytest.mark.asyncio
    async def test_remove_whitelist_success(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        """Test removing address from whitelist."""
        mock_provider = AsyncMock()
        mock_provider.remove_whitelist_address = AsyncMock(return_value={})

        with patch("app.api.whitelist.get_provider", return_value=mock_provider):
            response = await client.delete(
                "/whitelist/wl_123",
                params={"vault_id": str(test_vault.id)},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["whitelist_id"] == "wl_123"

    @pytest.mark.asyncio
    async def test_remove_whitelist_vault_not_found(self, client: AsyncClient):
        """Test removing whitelist entry for non-existent vault."""
        fake_vault_id = uuid4()

        response = await client.delete(
            "/whitelist/wl_123",
            params={"vault_id": str(fake_vault_id)},
        )

        assert response.status_code == 404
