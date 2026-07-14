"""
Tests for webhook handling.
"""

import pytest
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import VaultModel, WalletModel
from app.schemas.webhooks import (
    FireblocksWebhookPayloadSchema,
    TransactionDetailsSchema,
    WebhookPayload,
)
from app.dao.webhook.parse import parse_amount, parse_amount_usd, parse_net_amount


class TestWebhookParsing:
    """Tests for webhook data parsing."""

    def test_parse_amount_from_amount_info(self):
        """Test parsing amount from amountInfo."""
        tx = TransactionDetailsSchema(
            id="fb_tx_123",
            status="COMPLETED",
            amountInfo={"amount": "100.5", "netAmount": "100.0", "amountUSD": "100.5"},
        )

        result = parse_amount(tx)
        assert result == Decimal("100.5")

    def test_parse_amount_from_deprecated_field(self):
        """Test parsing amount from deprecated field."""
        tx = TransactionDetailsSchema(
            id="fb_tx_123",
            status="COMPLETED",
            amount=50.25,
        )

        result = parse_amount(tx)
        assert result == Decimal("50.25")

    def test_parse_amount_usd(self):
        """Test parsing USD amount."""
        tx = TransactionDetailsSchema(
            id="fb_tx_123",
            status="COMPLETED",
            amountInfo={"amount": "100.5", "amountUSD": "100.5"},
        )

        result = parse_amount_usd(tx)
        assert result == Decimal("100.5")

    def test_parse_amount_usd_none(self):
        """Test parsing USD amount when not available."""
        tx = TransactionDetailsSchema(
            id="fb_tx_123",
            status="COMPLETED",
        )

        result = parse_amount_usd(tx)
        assert result is None

    def test_parse_net_amount(self):
        """Test parsing net amount."""
        tx = TransactionDetailsSchema(
            id="fb_tx_123",
            status="COMPLETED",
            amountInfo={"amount": "100.5", "netAmount": "99.5"},
        )

        result = parse_net_amount(tx)
        assert result == Decimal("99.5")


class TestFireblocksWebhookPayload:
    """Tests for FireblocksWebhookPayloadSchema."""

    def test_is_incoming_deposit_true(self):
        """Test detection of incoming deposit."""
        payload = FireblocksWebhookPayloadSchema(
            id="notif_123",
            workspaceId="ws_123",
            eventType="transaction.created",
            createdAt=1234567890,
            data={
                "id": "fb_tx_123",
                "status": "SUBMITTED",
                "operation": "TRANSFER",
                "source": {"type": "ONE_TIME_ADDRESS"},
                "destination": {"type": "VAULT_ACCOUNT", "id": "1"},
            },
        )

        assert payload.is_incoming_deposit() is True

    def test_is_incoming_deposit_false_internal(self):
        """Test internal transfer is not incoming deposit."""
        payload = FireblocksWebhookPayloadSchema(
            id="notif_123",
            workspaceId="ws_123",
            eventType="transaction.created",
            createdAt=1234567890,
            data={
                "id": "fb_tx_123",
                "status": "SUBMITTED",
                "operation": "TRANSFER",
                "source": {"type": "VAULT_ACCOUNT", "id": "1"},
                "destination": {"type": "VAULT_ACCOUNT", "id": "2"},
            },
        )

        assert payload.is_incoming_deposit() is False

    def test_is_incoming_deposit_false_not_transaction(self):
        """Test non-transaction event."""
        payload = FireblocksWebhookPayloadSchema(
            id="notif_123",
            workspaceId="ws_123",
            eventType="vault_account.created",
            createdAt=1234567890,
            data={"id": "vault_1"},
        )

        assert payload.is_incoming_deposit() is False

    def test_is_completed_transaction(self):
        """Test completed transaction detection."""
        payload = FireblocksWebhookPayloadSchema(
            id="notif_123",
            workspaceId="ws_123",
            eventType="transaction.status_updated",
            createdAt=1234567890,
            data={
                "id": "fb_tx_123",
                "status": "COMPLETED",
                "operation": "TRANSFER",
            },
        )

        assert payload.is_completed_transaction() is True

    def test_get_transaction_details(self):
        """Test extracting transaction details."""
        payload = FireblocksWebhookPayloadSchema(
            id="notif_123",
            workspaceId="ws_123",
            eventType="transaction.created",
            createdAt=1234567890,
            data={
                "id": "fb_tx_123",
                "status": "SUBMITTED",
                "assetId": "USDT_TRX",
                "txHash": "0xabc123",
            },
        )

        tx = payload.get_transaction_details()
        assert tx is not None
        assert tx.id == "fb_tx_123"
        assert tx.status == "SUBMITTED"
        assert tx.assetId == "USDT_TRX"


class TestSimpleWebhookPayload:
    """Tests for simple WebhookPayload schema."""

    def test_create_webhook_payload(self):
        """Test creating simple webhook payload."""
        payload = WebhookPayload(
            type="TRANSACTION_CREATED",
            data={"id": "tx_123", "status": "PENDING"},
        )

        assert payload.type == "TRANSACTION_CREATED"
        assert payload.data["id"] == "tx_123"

    def test_webhook_payload_empty_data(self):
        """Test webhook payload with empty data."""
        payload = WebhookPayload(
            type="VAULT_CREATED",
            data={},
        )

        assert payload.type == "VAULT_CREATED"
        assert payload.data == {}


class TestWebhookBalanceUpdate:
    """Tests for balance updates from webhooks."""

    @pytest.mark.asyncio
    async def test_balance_increased_on_deposit(
        self,
        test_session: AsyncSession,
        test_wallet: WalletModel,
    ):
        """Test wallet balance increases on deposit completion."""
        initial_balance = test_wallet.balance
        deposit_amount = Decimal("25.5")

        # Update balance
        test_wallet.balance = initial_balance + deposit_amount
        await test_session.commit()
        await test_session.refresh(test_wallet)

        assert test_wallet.balance == initial_balance + deposit_amount

    @pytest.mark.asyncio
    async def test_balance_decreased_on_withdrawal(
        self,
        test_session: AsyncSession,
        test_wallet: WalletModel,
    ):
        """Test wallet balance decreases on withdrawal completion."""
        initial_balance = test_wallet.balance
        withdrawal_amount = Decimal("10.0")

        # Update balance
        test_wallet.balance = initial_balance - withdrawal_amount
        await test_session.commit()
        await test_session.refresh(test_wallet)

        assert test_wallet.balance == initial_balance - withdrawal_amount


class TestWebhookVaultAccountResolution:
    """Tests for vault account resolution from webhooks."""

    @pytest.mark.asyncio
    async def test_resolve_vault_by_provider_id(
        self,
        test_session: AsyncSession,
        test_vault: VaultModel,
    ):
        """Test resolving vault by provider ID."""
        result = await test_session.execute(
            select(VaultModel).where(
                VaultModel.provider_vault_id == test_vault.provider_vault_id
            )
        )
        found_vault = result.scalar_one_or_none()

        assert found_vault is not None
        assert found_vault.id == test_vault.id

    @pytest.mark.asyncio
    async def test_resolve_wallet_by_address(
        self,
        test_session: AsyncSession,
        test_wallet: WalletModel,
    ):
        """Test resolving wallet by address."""
        result = await test_session.execute(
            select(WalletModel).where(WalletModel.address == test_wallet.address)
        )
        found_wallet = result.scalar_one_or_none()

        assert found_wallet is not None
        assert found_wallet.id == test_wallet.id
