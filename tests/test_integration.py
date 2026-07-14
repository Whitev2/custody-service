import pytest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VaultModel, AssetModel, WalletModel, TransactionModel


class TestVaultWorkflow:

    @pytest.mark.asyncio
    async def test_full_vault_lifecycle(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_asset: AssetModel,
    ):
        # create -> add asset -> get info
        mock_provider = AsyncMock()
        mock_provider.create_vault = AsyncMock(
            return_value={
                "id": "fb_vault_lifecycle",
                "name": "LIFECYCLE_VAULT",
            }
        )
        mock_provider.activate_asset = AsyncMock(
            return_value={
                "address": "TLifecycleAddress12345678901234567",
                "legacyAddress": None,
                "tag": None,
            }
        )
        mock_provider.add_whitelist_address = AsyncMock(return_value={"id": "wl_lc"})

        with patch("app.dao.vault.get_provider", return_value=mock_provider):
            with patch("app.dao.asset.get_provider", return_value=mock_provider):
                response = await client.post(
                    "/vault/create",
                    json={
                        "name": "LIFECYCLE_VAULT",
                        "assets": [
                            {
                                "blockchain": test_asset.blockchain,
                                "currency": test_asset.currency,
                                "network": test_asset.network,
                            }
                        ],
                        "auto_fuel": True,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        vault_id = data["vault_id"]

        response = await client.get(f"/vault/{vault_id}/info")
        assert response.status_code == 200
        info = response.json()
        assert info["name"] == "LIFECYCLE_VAULT"

        response = await client.get("/vault/list")
        assert response.status_code == 200
        assert any(v["vault_id"] == vault_id for v in response.json()["vaults"])


class TestTransferWorkflow:

    @pytest.mark.asyncio
    async def test_internal_transfer_full_flow(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_vault: VaultModel,
        test_asset: AssetModel,
        test_wallet: WalletModel,
    ):
        dest_vault = VaultModel(
            id=uuid4(),
            provider_vault_id=f"fb_vault_dest_{uuid4().hex[:8]}",
            name="DEST_VAULT_INT",
            status="available",
            is_active=True,
        )
        test_session.add(dest_vault)
        await test_session.flush()

        dest_wallet = WalletModel(
            id=uuid4(),
            vault_id=dest_vault.id,
            asset_id=test_asset.id,
            address=f"TDestInt{uuid4().hex[:24]}",
            balance=Decimal("0"),
        )
        test_session.add(dest_wallet)
        await test_session.commit()

        mock_provider = AsyncMock()
        mock_provider.create_transaction = AsyncMock(
            return_value={
                "id": f"fb_tx_int_{uuid4().hex[:8]}",
                "txHash": None,
                "status": "SUBMITTED",
            }
        )
        mock_provider.get_whitelist_addresses = AsyncMock(
            return_value=[{"address": dest_wallet.address, "id": "wl_dest"}]
        )

        transfer_amount = Decimal("20.0")

        with patch("app.api.transfer.get_provider", return_value=mock_provider):
            response = await client.post(
                "/transfer/internal/create",
                json={
                    "from_vault_id": str(test_vault.id),
                    "to_vault_id": str(dest_vault.id),
                    "asset_id": str(test_asset.id),
                    "amount": str(transfer_amount),
                    "note": "Integration test transfer",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["is_internal"] is True
        assert data["status"] == "SUBMITTED"


class TestAssetWorkflow:

    @pytest.mark.asyncio
    async def test_get_asset_addresses_across_vaults(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_asset: AssetModel,
    ):
        wallets = []
        for i in range(3):
            vault = VaultModel(
                id=uuid4(),
                provider_vault_id=f"fb_vault_multi_{i}_{uuid4().hex[:4]}",
                name=f"MULTI_VAULT_{i}",
                status="available",
                is_active=True,
            )
            test_session.add(vault)
            await test_session.flush()

            wallet = WalletModel(
                id=uuid4(),
                vault_id=vault.id,
                asset_id=test_asset.id,
                address=f"TMulti{i}{uuid4().hex[:26]}",
                balance=Decimal(str(i * 100)),
            )
            test_session.add(wallet)
            wallets.append(wallet)

        await test_session.commit()

        response = await client.get(f"/asset/{test_asset.id}/addresses")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 3

    @pytest.mark.asyncio
    async def test_get_asset_transaction_history(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        test_vault: VaultModel,
        test_wallet: WalletModel,
        test_asset: AssetModel,
    ):
        for i in range(5):
            tx = TransactionModel(
                id=uuid4(),
                provider_tx_id=f"fb_tx_hist_{i}_{uuid4().hex[:4]}",
                tx_hash=f"0xhist{i}{uuid4().hex}",
                vault_id=test_vault.id,
                wallet_id=test_wallet.id,
                asset_id=test_asset.id,
                amount=Decimal(str((i + 1) * 10)),
                amount_usd=Decimal(str((i + 1) * 10)),
                status="COMPLETED",
                is_internal=False,
                source_address=f"TSource{i}",
                destination_address=test_wallet.address,
            )
            test_session.add(tx)

        await test_session.commit()

        response = await client.get(f"/asset/{test_asset.id}/history")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 5


class TestHealthEndpoint:

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient):
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "custody_v2"
