from datetime import datetime
from uuid import uuid4


class TestVaultSchemas:

    def test_vault_create_request_valid(self):
        from app.schemas.vault import VaultCreateRequest

        request = VaultCreateRequest(
            name="TEST_VAULT",
            assets=[
                {
                    "blockchain": "TRON",
                    "currency": "USDT",
                    "network": "TRC20",
                }
            ],
            auto_fuel=True,
        )

        assert request.name == "TEST_VAULT"
        assert len(request.assets) == 1
        assert request.auto_fuel is True

    def test_vault_create_request_no_name(self):
        from app.schemas.vault import VaultCreateRequest

        request = VaultCreateRequest(
            assets=[],
            auto_fuel=False,
        )

        # name = None, автогенерится в DAO
        assert request.name is None

    def test_vault_create_request_empty_assets(self):
        from app.schemas.vault import VaultCreateRequest

        request = VaultCreateRequest(
            name="EMPTY_VAULT",
            assets=[],
        )

        assert request.assets == []

    def test_vault_info_response(self):
        from app.schemas.vault import VaultInfoResponse

        response = VaultInfoResponse(
            vault_id=uuid4(),
            provider_vault_id="fb_vault_123",
            name="TEST_VAULT",
            vault_type="regular",
            status="available",
            is_active=True,
            wallets=[],
            created_at=datetime.now(),
        )

        assert response.status == "available"
        assert response.is_active is True


class TestAssetSchemas:

    def test_asset_create_request(self):
        from app.schemas.asset import AssetCreateRequest

        request = AssetCreateRequest(
            vault_id=uuid4(),
            asset_id=uuid4(),
        )

        assert request.vault_id is not None
        assert request.asset_id is not None

    def test_asset_info_response(self):
        from app.schemas.asset import AssetInfoResponse

        response = AssetInfoResponse(
            asset_id=uuid4(),
            wallet_id=uuid4(),
            vault_id=uuid4(),
            address="TAddress123456789012345678901234",
            legacy_address=None,
            tag=None,
            balance="100.5",
            blockchain="TRON",
            currency="USDT",
            network="TRC20",
            created_at=datetime.now(),
        )

        assert response.address.startswith("T")
        assert response.balance == "100.5"


class TestTransferSchemas:

    def test_internal_transfer_request(self):
        from app.schemas.transfer import InternalTransferRequest

        request = InternalTransferRequest(
            from_vault_id=uuid4(),
            to_vault_id=uuid4(),
            asset_id=uuid4(),
            amount="50.0",
            note="Test transfer",
        )

        assert request.amount == "50.0"
        assert request.note == "Test transfer"

    def test_external_transfer_request(self):
        from app.schemas.transfer import ExternalTransferRequest

        request = ExternalTransferRequest(
            from_vault_id=uuid4(),
            asset_id=uuid4(),
            to_address="TExternalAddress123456789012345678",
            amount="25.0",
        )

        assert request.to_address == "TExternalAddress123456789012345678"
        assert request.amount == "25.0"

    def test_transfer_request_invalid_amount(self):
        from app.schemas.transfer import InternalTransferRequest

        # amount - строка, валидация лояльная: "-10.0" проходит
        request = InternalTransferRequest(
            from_vault_id=uuid4(),
            to_vault_id=uuid4(),
            asset_id=uuid4(),
            amount="-10.0",
        )
        assert request.amount == "-10.0"

    def test_transfer_response(self):
        from app.schemas.transfer import TransferResponse

        response = TransferResponse(
            transfer_id=uuid4(),
            provider_tx_id="fb_tx_123",
            from_vault_id=uuid4(),
            to_vault_id=uuid4(),
            to_address=None,
            asset_id=uuid4(),
            amount="50.0",
            status="SUBMITTED",
            is_internal=True,
            created_at=datetime.now(),
        )

        assert response.is_internal is True
        assert response.status == "SUBMITTED"


class TestWhitelistSchemas:

    def test_whitelist_add_request(self):
        from app.schemas.whitelist import WhitelistAddRequest

        request = WhitelistAddRequest(
            vault_id=uuid4(),
            asset_id=uuid4(),
            address="TWhitelistAddress123456789012345",
            description="Test entry",
        )

        assert request.address.startswith("T")
        assert request.description == "Test entry"

    def test_whitelist_check_request(self):
        from app.schemas.whitelist import WhitelistCheckRequest

        request = WhitelistCheckRequest(
            vault_id=uuid4(),
            asset_id=uuid4(),
            address="TCheckAddress12345678901234567890",
        )

        assert request.address is not None

    def test_whitelist_check_response(self):
        from app.schemas.whitelist import WhitelistCheckResponse

        response = WhitelistCheckResponse(
            is_whitelisted=True,
            whitelist_id="wl_123",
            address="TWhitelistedAddress12345678901234",
        )

        assert response.is_whitelisted is True
        assert response.whitelist_id == "wl_123"


class TestWebhookSchemas:

    def test_webhook_payload_transaction(self):
        from app.schemas.webhooks import WebhookPayload

        payload = WebhookPayload(
            type="TRANSACTION_CREATED",
            data={
                "id": "fb_tx_123",
                "status": "SUBMITTED",
                "assetId": "USDT_TRX",
            },
        )

        assert payload.type == "TRANSACTION_CREATED"
        assert payload.data["id"] == "fb_tx_123"

    def test_webhook_payload_vault_account(self):
        from app.schemas.webhooks import WebhookPayload

        payload = WebhookPayload(
            type="VAULT_ACCOUNT_ASSET_CREATED",
            data={
                "vaultAccountId": "1",
                "assetId": "USDT_TRX",
                "address": "TNewAddress123",
            },
        )

        assert payload.type == "VAULT_ACCOUNT_ASSET_CREATED"
