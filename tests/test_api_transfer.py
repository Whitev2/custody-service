"""
Tests for Transfer API endpoints.
"""

import pytest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VaultModel, AssetModel, WalletModel


class TestTransferAPI:
    """Tests for /transfer endpoints."""

    @pytest.mark.asyncio
    async def test_create_internal_transfer_success(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_vault: VaultModel,
        test_asset: AssetModel,
        test_wallet: WalletModel,
    ):
        """Test successful internal transfer."""
        # Create destination vault and wallet
        dest_vault = VaultModel(
            id=uuid4(),
            provider_vault_id=f"fb_vault_dest_{uuid4().hex[:8]}",
            name="DEST_VAULT",
            status="available",
            is_active=True,
        )
        test_session.add(dest_vault)
        await test_session.flush()

        dest_wallet = WalletModel(
            id=uuid4(),
            vault_id=dest_vault.id,
            asset_id=test_asset.id,
            address=f"TDest{uuid4().hex[:28]}",
            balance=Decimal("0"),
        )
        test_session.add(dest_wallet)
        await test_session.commit()

        # Mock provider
        mock_provider = AsyncMock()
        mock_provider.create_transaction = AsyncMock(
            return_value={
                "id": f"fb_tx_{uuid4().hex[:8]}",
                "txHash": None,
                "status": "SUBMITTED",
            }
        )
        mock_provider.get_whitelist_addresses = AsyncMock(
            return_value=[{"address": dest_wallet.address, "id": "wl_123"}]
        )

        with patch("app.api.transfer.get_provider", return_value=mock_provider):
            response = await client.post(
                "/transfer/internal/create",
                json={
                    "from_vault_id": str(test_vault.id),
                    "to_vault_id": str(dest_vault.id),
                    "asset_id": str(test_asset.id),
                    "amount": "50.0",
                    "note": "Test transfer",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["is_internal"] is True
        assert data["status"] == "SUBMITTED"
        assert data["from_vault_id"] == str(test_vault.id)
        assert data["to_vault_id"] == str(dest_vault.id)

    @pytest.mark.asyncio
    async def test_create_internal_transfer_insufficient_balance(
        self,
        client: AsyncClient,
        test_vault: VaultModel,
        test_asset: AssetModel,
        test_wallet: WalletModel,
    ):
        """Test internal transfer with insufficient balance."""
        dest_vault_id = uuid4()

        response = await client.post(
            "/transfer/internal/create",
            json={
                "from_vault_id": str(test_vault.id),
                "to_vault_id": str(dest_vault_id),
                "asset_id": str(test_asset.id),
                "amount": "10000.0",  # More than wallet balance
            },
        )

        assert response.status_code == 400
        assert "insufficient" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_internal_transfer_vault_not_found(
        self, client: AsyncClient, test_asset: AssetModel
    ):
        """Test internal transfer with non-existent vault."""
        fake_vault_id = uuid4()

        response = await client.post(
            "/transfer/internal/create",
            json={
                "from_vault_id": str(fake_vault_id),
                "to_vault_id": str(uuid4()),
                "asset_id": str(test_asset.id),
                "amount": "10.0",
            },
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_create_external_transfer_success(
        self,
        client: AsyncClient,
        test_vault: VaultModel,
        test_asset: AssetModel,
        test_wallet: WalletModel,
    ):
        """Test successful external transfer."""
        mock_provider = AsyncMock()
        mock_provider.create_transaction = AsyncMock(
            return_value={
                "id": f"fb_tx_{uuid4().hex[:8]}",
                "txHash": None,
                "status": "PENDING_SIGNATURE",
            }
        )

        with patch("app.api.transfer.get_provider", return_value=mock_provider):
            response = await client.post(
                "/transfer/external/create",
                json={
                    "from_vault_id": str(test_vault.id),
                    "asset_id": str(test_asset.id),
                    "to_address": "TExternalAddress123456789012345678",
                    "amount": "25.0",
                    "note": "External withdrawal",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["is_internal"] is False
        assert data["to_address"] == "TExternalAddress123456789012345678"

    @pytest.mark.asyncio
    async def test_create_external_transfer_insufficient_balance(
        self,
        client: AsyncClient,
        test_vault: VaultModel,
        test_asset: AssetModel,
        test_wallet: WalletModel,
    ):
        """Test external transfer with insufficient balance."""
        response = await client.post(
            "/transfer/external/create",
            json={
                "from_vault_id": str(test_vault.id),
                "asset_id": str(test_asset.id),
                "to_address": "TExternalAddress123456789012345678",
                "amount": "99999.0",
            },
        )

        assert response.status_code == 400
        assert "insufficient" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_external_transfer_with_workflow(
        self,
        client: AsyncClient,
        test_vault: VaultModel,
        test_asset: AssetModel,
        test_wallet: WalletModel,
    ):
        """Test external transfer with workflow ID."""
        workflow_id = str(uuid4())

        mock_provider = AsyncMock()
        mock_provider.create_transaction = AsyncMock(
            return_value={
                "id": f"fb_tx_{uuid4().hex[:8]}",
                "txHash": None,
                "status": "PENDING_SIGNATURE",
            }
        )

        with patch("app.api.transfer.get_provider", return_value=mock_provider):
            response = await client.post(
                "/transfer/external/create",
                json={
                    "from_vault_id": str(test_vault.id),
                    "asset_id": str(test_asset.id),
                    "to_address": "TExternalAddress123456789012345678",
                    "amount": "10.0",
                    "workflow_id": workflow_id,
                },
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_internal_transfer_to_address_not_in_whitelist(
        self,
        client: AsyncClient,
        test_vault: VaultModel,
        test_asset: AssetModel,
        test_wallet: WalletModel,
    ):
        """Test internal transfer to address not in whitelist."""
        mock_provider = AsyncMock()
        mock_provider.get_whitelist_addresses = AsyncMock(
            return_value=[]
        )  # Empty whitelist

        with patch("app.api.transfer.get_provider", return_value=mock_provider):
            response = await client.post(
                "/transfer/internal/create",
                json={
                    "from_vault_id": str(test_vault.id),
                    "to_address": "TNotWhitelistedAddress12345678901",
                    "asset_id": str(test_asset.id),
                    "amount": "10.0",
                },
            )

        assert response.status_code == 400
        assert "whitelist" in response.json()["detail"].lower()
