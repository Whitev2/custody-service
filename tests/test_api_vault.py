import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from httpx import AsyncClient

from app.models import VaultModel, AssetModel


class TestVaultAPI:

    @pytest.mark.asyncio
    async def test_create_vault_success(
        self, client: AsyncClient, test_asset: AssetModel
    ):
        mock_provider = AsyncMock()
        mock_provider.create_vault = AsyncMock(
            return_value={
                "id": "fb_vault_12345",
                "name": "TEST_VAULT",
            }
        )
        mock_provider.activate_asset = AsyncMock(
            return_value={
                "address": "TTestAddress123456789012345678901",
                "legacyAddress": None,
                "tag": None,
            }
        )
        mock_provider.add_whitelist_address = AsyncMock(return_value={"id": "wl_123"})

        with patch(
            "app.services.custody.factory.get_provider", return_value=mock_provider
        ):
            with patch("app.dao.vault.get_provider", return_value=mock_provider):
                with patch("app.dao.asset.get_provider", return_value=mock_provider):
                    response = await client.post(
                        "/vault/create",
                        json={
                            "name": "TEST_VAULT_001",
                            "assets": [
                                {
                                    "blockchain": "TRON",
                                    "currency": "USDT",
                                    "network": "TRC20",
                                }
                            ],
                            "auto_fuel": True,
                        },
                    )

        assert response.status_code == 200
        data = response.json()
        assert "vault_id" in data
        assert data["name"] == "TEST_VAULT_001"
        assert data["status"] == "available"

    @pytest.mark.asyncio
    async def test_create_vault_no_assets(self, client: AsyncClient):
        mock_provider = AsyncMock()
        mock_provider.create_vault = AsyncMock(
            return_value={
                "id": "fb_vault_12345",
                "name": "TEST_VAULT",
            }
        )

        with patch(
            "app.services.custody.factory.get_provider", return_value=mock_provider
        ):
            with patch("app.dao.vault.get_provider", return_value=mock_provider):
                response = await client.post(
                    "/vault/create",
                    json={
                        "name": "EMPTY_VAULT",
                        "assets": [],
                        "auto_fuel": False,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "EMPTY_VAULT"
        assert data["wallets"] == []

    @pytest.mark.asyncio
    async def test_get_vault_info_success(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        response = await client.get(f"/vault/{test_vault.id}/info")

        assert response.status_code == 200
        data = response.json()
        assert str(data["vault_id"]) == str(test_vault.id)
        assert data["name"] == test_vault.name
        assert data["status"] == test_vault.status

    @pytest.mark.asyncio
    async def test_get_vault_info_not_found(self, client: AsyncClient):
        fake_id = uuid4()
        response = await client.get(f"/vault/{fake_id}/info")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_list_vaults_empty(self, client: AsyncClient):
        response = await client.get("/vault/list")

        assert response.status_code == 200
        data = response.json()
        assert "vaults" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_list_vaults_with_data(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        response = await client.get("/vault/list")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["vaults"]) >= 1

    @pytest.mark.asyncio
    async def test_list_vaults_pagination(
        self, client: AsyncClient, test_vault: VaultModel
    ):
        response = await client.get("/vault/list?skip=0&limit=10")

        assert response.status_code == 200
        data = response.json()
        assert "vaults" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_create_vault_auto_generated_name(self, client: AsyncClient):
        mock_provider = AsyncMock()
        mock_provider.create_vault = AsyncMock(
            return_value={
                "id": "fb_vault_auto",
                "name": "AUTO_VAULT",
            }
        )

        with patch(
            "app.services.custody.factory.get_provider", return_value=mock_provider
        ):
            with patch("app.dao.vault.get_provider", return_value=mock_provider):
                response = await client.post(
                    "/vault/create",
                    json={
                        "assets": [],
                        "auto_fuel": True,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert data["name"].startswith("VAULT_")
