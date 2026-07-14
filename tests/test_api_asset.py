import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from httpx import AsyncClient

from app.models import VaultModel, AssetModel, WalletModel, TransactionModel


class TestAssetAPI:
    @pytest.mark.asyncio
    async def test_create_asset_success(
        self, client: AsyncClient, test_vault: VaultModel, test_asset: AssetModel
    ):
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
        response = await client.get(f"/asset/{test_transaction.asset_id}/history")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["transactions"]) >= 1

    @pytest.mark.asyncio
    async def test_get_asset_history_pagination(
        self, client: AsyncClient, test_transaction: TransactionModel
    ):
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
        response = await client.get(f"/asset/{test_asset.id}/addresses")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
