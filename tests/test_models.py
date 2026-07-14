import pytest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import VaultModel, AssetModel, WalletModel, TransactionModel


class TestVaultModel:

    @pytest.mark.asyncio
    async def test_create_vault(self, test_session: AsyncSession):
        vault = VaultModel(
            id=uuid4(),
            provider_vault_id="fb_vault_test123",
            name="TEST_VAULT",
            status="available",
            is_active=True,
        )
        test_session.add(vault)
        await test_session.commit()

        result = await test_session.execute(
            select(VaultModel).where(VaultModel.id == vault.id)
        )
        saved_vault = result.scalar_one()

        assert saved_vault.name == "TEST_VAULT"
        assert saved_vault.status == "available"
        assert saved_vault.is_active is True

    @pytest.mark.asyncio
    async def test_vault_wallets_relationship(
        self, test_session: AsyncSession, test_vault: VaultModel, test_wallet: WalletModel
    ):
        await test_session.refresh(test_vault, ["wallets"])
        
        assert len(test_vault.wallets) >= 1
        assert test_wallet in test_vault.wallets

    @pytest.mark.asyncio
    async def test_vault_default_values(self, test_session: AsyncSession):
        vault = VaultModel(
            id=uuid4(),
            provider_vault_id="fb_vault_defaults",
            name="DEFAULTS_VAULT",
        )
        test_session.add(vault)
        await test_session.commit()
        await test_session.refresh(vault)

        assert vault.status == "creating"
        assert vault.is_active is True
        assert vault.created_at is not None


class TestAssetModel:

    @pytest.mark.asyncio
    async def test_create_asset(self, test_session: AsyncSession):
        asset = AssetModel(
            id=uuid4(),
            asset="BTC_NATIVE",
            currency="BTC",
            blockchain="BITCOIN",
            network="NATIVE",
            decimals=8,
            is_active=True,
            is_testnet=False,
        )
        test_session.add(asset)
        await test_session.commit()

        result = await test_session.execute(
            select(AssetModel).where(AssetModel.id == asset.id)
        )
        saved_asset = result.scalar_one()

        assert saved_asset.currency == "BTC"
        assert saved_asset.blockchain == "BITCOIN"
        assert saved_asset.decimals == 8

    @pytest.mark.asyncio
    async def test_asset_wallets_relationship(
        self, test_session: AsyncSession, test_asset: AssetModel, test_wallet: WalletModel
    ):
        await test_session.refresh(test_asset, ["wallets"])
        
        assert len(test_asset.wallets) >= 1

    @pytest.mark.asyncio
    async def test_asset_testnet_flag(self, test_session: AsyncSession):
        asset = AssetModel(
            id=uuid4(),
            asset="ETH_TEST5",
            currency="ETH",
            blockchain="ETHEREUM",
            network="SEPOLIA",
            decimals=18,
            is_active=True,
            is_testnet=True,
        )
        test_session.add(asset)
        await test_session.commit()
        await test_session.refresh(asset)

        assert asset.is_testnet is True


class TestWalletModel:

    @pytest.mark.asyncio
    async def test_create_wallet(
        self, test_session: AsyncSession, test_vault: VaultModel, test_asset: AssetModel
    ):
        wallet = WalletModel(
            id=uuid4(),
            vault_id=test_vault.id,
            asset_id=test_asset.id,
            address="TNewWalletAddress123456789012345678",
            balance=Decimal("0"),
        )
        test_session.add(wallet)
        await test_session.commit()

        result = await test_session.execute(
            select(WalletModel).where(WalletModel.id == wallet.id)
        )
        saved_wallet = result.scalar_one()

        assert saved_wallet.address == "TNewWalletAddress123456789012345678"
        assert saved_wallet.balance == Decimal("0")

    @pytest.mark.asyncio
    async def test_wallet_with_tag(
        self, test_session: AsyncSession, test_vault: VaultModel, test_asset: AssetModel
    ):
        wallet = WalletModel(
            id=uuid4(),
            vault_id=test_vault.id,
            asset_id=test_asset.id,
            address="rXRPAddress123456789",
            tag="12345678",
            balance=Decimal("0"),
        )
        test_session.add(wallet)
        await test_session.commit()
        await test_session.refresh(wallet)

        assert wallet.tag == "12345678"

    @pytest.mark.asyncio
    async def test_wallet_balance_update(
        self, test_session: AsyncSession, test_wallet: WalletModel
    ):
        test_wallet.balance = Decimal("500.50")
        await test_session.commit()
        await test_session.refresh(test_wallet)

        assert test_wallet.balance == Decimal("500.50")

    @pytest.mark.asyncio
    async def test_wallet_relationships(
        self, test_session: AsyncSession, test_wallet: WalletModel
    ):
        await test_session.refresh(test_wallet, ["vault", "asset"])
        
        assert test_wallet.vault is not None
        assert test_wallet.asset is not None


class TestTransactionModel:

    @pytest.mark.asyncio
    async def test_create_transaction(
        self,
        test_session: AsyncSession,
        test_vault: VaultModel,
        test_wallet: WalletModel,
        test_asset: AssetModel,
    ):
        tx = TransactionModel(
            id=uuid4(),
            provider_tx_id="fb_tx_test_123",
            tx_hash="0xabcdef123456",
            vault_id=test_vault.id,
            wallet_id=test_wallet.id,
            asset_id=test_asset.id,
            amount=Decimal("100.0"),
            amount_usd=Decimal("100.0"),
            status="COMPLETED",
            num_confirmations=12,
            is_internal=False,
            source_address="TSourceAddress123456789012345678",
            destination_address=test_wallet.address,
        )
        test_session.add(tx)
        await test_session.commit()

        result = await test_session.execute(
            select(TransactionModel).where(TransactionModel.id == tx.id)
        )
        saved_tx = result.scalar_one()

        assert saved_tx.tx_hash == "0xabcdef123456"
        assert saved_tx.amount == Decimal("100.0")
        assert saved_tx.status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_transaction_internal_flag(
        self,
        test_session: AsyncSession,
        test_vault: VaultModel,
        test_wallet: WalletModel,
        test_asset: AssetModel,
    ):
        tx = TransactionModel(
            id=uuid4(),
            provider_tx_id="fb_tx_internal",
            vault_id=test_vault.id,
            wallet_id=test_wallet.id,
            asset_id=test_asset.id,
            amount=Decimal("50.0"),
            status="PENDING",
            is_internal=True,
            source_address="TSource123",
            destination_address="TDest456",
        )
        test_session.add(tx)
        await test_session.commit()
        await test_session.refresh(tx)

        assert tx.is_internal is True

    @pytest.mark.asyncio
    async def test_transaction_relationships(
        self, test_session: AsyncSession, test_transaction: TransactionModel
    ):
        await test_session.refresh(test_transaction, ["vault", "wallet", "asset"])
        
        assert test_transaction.vault is not None
        assert test_transaction.wallet is not None
        assert test_transaction.asset is not None

    @pytest.mark.asyncio
    async def test_transaction_status_update(
        self, test_session: AsyncSession, test_transaction: TransactionModel
    ):
        test_transaction.status = "FAILED"
        test_transaction.failure_reason = "Insufficient gas"
        await test_session.commit()
        await test_session.refresh(test_transaction)

        assert test_transaction.status == "FAILED"
        assert test_transaction.failure_reason == "Insufficient gas"
