from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import log
from app.enums.fireblocks import TransactionStatusEnum
from app.models import TransactionModel, WalletModel, AssetModel
from app.schemas.webhooks import TransactionDetailsSchema, WebhookProcessResultSchema

from .parse import parse_net_amount_decimal
from .common import identify_wallet, WalletInfo
from .utils import map_fireblocks_to_invoice_status


async def update_transaction(
    db: AsyncSession,
    transaction: TransactionModel,
    tx: TransactionDetailsSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    log.info(
        f"🔄 Updating transaction: id={transaction.id}, "
        f"old_status={transaction.status}, new_status={tx.status}"
    )

    old_status = transaction.status

    transaction.status = tx.status
    transaction.tx_hash = tx.txHash or transaction.tx_hash
    transaction.num_confirmations = tx.numOfConfirmations
    transaction.raw_webhook_data = raw_body

    if tx.amountInfo:
        if tx.amountInfo.amount:
            transaction.amount = Decimal(tx.amountInfo.amount)
        if tx.amountInfo.amountUSD:
            transaction.amount_usd = Decimal(tx.amountInfo.amountUSD)

    log.info(
        f"✅ Transaction updated: id={transaction.id}, "
        f"status={old_status} -> {tx.status}"
    )

    # Всегда шлём статус в backend; баланс обновляем только на COMPLETED
    wallet_info = await identify_wallet(db, tx)
    if wallet_info:
        if tx.status == TransactionStatusEnum.COMPLETED.value:
            await update_wallet_balance(db, wallet_info, tx)
            await process_pending_balance_queue_for_asset(db, wallet_info)
        await notify_backend_about_deposit(db, wallet_info, tx)

    return WebhookProcessResultSchema(
        status="updated",
        transaction_id=str(transaction.id),
        provider_tx_id=tx.id,
        vault_id=str(transaction.vault_id) if transaction.vault_id else None,
        wallet_id=str(transaction.wallet_id) if transaction.wallet_id else None,
        asset_id=str(transaction.asset_id) if transaction.asset_id else None,
    )


async def update_wallet_balance(
    db: AsyncSession, wallet_info: WalletInfo, tx: TransactionDetailsSchema
) -> None:
    wallet_id = wallet_info.get("wallet_id")
    asset_id = wallet_info.get("asset_id")
    vault_id = wallet_info.get("vault_id")

    wallet: WalletModel | None = None
    if wallet_id:
        wallet = await db.get(WalletModel, wallet_id)
    elif vault_id and asset_id:
        stmt = select(WalletModel).where(
            WalletModel.vault_id == vault_id, WalletModel.asset_id == asset_id
        )
        result = await db.execute(stmt)
        wallet = result.scalar_one_or_none()

    if not wallet:
        log.warning(
            f"⚠️ Failed to update balance: wallet not found "
            f"(vault_id={vault_id}, asset_id={asset_id}, wallet_id={wallet_id})"
        )
        return

    new_balance = parse_net_amount_decimal(tx)
    try:
        old_balance = wallet.balance
        wallet.balance = new_balance
        await db.flush()
        log.info(
            f"💰 Updated wallet balance {wallet.id}: {old_balance} -> {new_balance}, "
            f"asset_id={asset_id}, vault_id={vault_id}"
        )
    except Exception as e:
        log.error(f"❌ Error updating wallet balance {wallet.id}: {e}")


async def notify_backend_about_deposit(
    db: AsyncSession, wallet_info: WalletInfo, tx: TransactionDetailsSchema
) -> None:
    """Шлём депозит в backend через очередь custody.webhook."""
    from app.broker.publisher import publish_custody_webhook

    vault_id = wallet_info.get("vault_id")
    blockchain = wallet_info.get("blockchain")
    currency = wallet_info.get("currency")
    asset_id = tx.assetId

    if not all([vault_id, blockchain, currency]):
        log.warning(
            f"⚠️ Недостаточно данных для отправки в backend: "
            f"vault_id={vault_id}, blockchain={blockchain}, currency={currency}"
        )
        return

    amount = parse_net_amount_decimal(tx)
    invoice_status = map_fireblocks_to_invoice_status(tx.status)

    # network берём из AssetModel, а не парсим assetId
    network = "BASE_ASSET"
    if wallet_info.get("asset_id"):
        asset_stmt = select(AssetModel).where(AssetModel.id == wallet_info["asset_id"])
        asset_result = await db.execute(asset_stmt)
        asset = asset_result.scalar_one_or_none()
        if asset and asset.network:
            network = asset.network

    success = await publish_custody_webhook(
        custody_vault_id=vault_id,
        amount=amount,
        blockchain=blockchain,
        currency=currency,
        network=network,
        status=invoice_status.value,
        tx_hash=tx.txHash,
        confirmations=tx.numOfConfirmations,
        asset_id=asset_id,
    )

    if not success:
        log.error(
            f"❌ Failed to publish custody webhook: vault_id={vault_id}, "
            f"tx_hash={tx.txHash}"
        )


async def process_pending_balance_queue_for_asset(
    db: AsyncSession,
    wallet_info: WalletInfo,
) -> None:
    """Разбираем pending_balance трансферы после депозита — резервируем баланс."""
    from sqlalchemy import select
    from app.models.transfer import TransferModel
    from app.enums.status import TransferStatus
    from app.dao.transfer import process_pending_balance_transfer
    from app.broker.publisher import publish_transfer_created
    
    asset_id = wallet_info.get("asset_id")
    blockchain = wallet_info.get("blockchain")
    
    if not asset_id or not blockchain:
        return

    asset = await db.get(AssetModel, asset_id)
    if not asset:
        return

    stmt = (
        select(TransferModel)
        .where(
            TransferModel.status == TransferStatus.PENDING_BALANCE.value,
            TransferModel.blockchain.ilike(asset.blockchain),
            TransferModel.contract_address == asset.contract_address,
        )
        .order_by(TransferModel.created_at.asc())
        .limit(10)
    )
    result = await db.execute(stmt)
    transfers = result.scalars().all()
    
    if not transfers:
        return
    
    log.info(f"🔄 Processing {len(transfers)} pending_balance transfers for {asset.currency}")
    
    for transfer in transfers:
        try:
            success = await process_pending_balance_transfer(db, transfer)
            if success:
                source_vault_id = None
                fireblocks_asset_id = None
                asset_model = None
                
                if transfer.vault_id:
                    from app.models import VaultModel
                    vault = await db.get(VaultModel, transfer.vault_id)
                    if vault:
                        source_vault_id = vault.provider_vault_id
                if transfer.asset_id:
                    asset_model = await db.get(AssetModel, transfer.asset_id)
                    if asset_model:
                        fireblocks_asset_id = asset_model.asset

                await publish_transfer_created(
                    request_id=transfer.request_id,
                    destination_address=transfer.destination_address,
                    destination_tag=transfer.destination_tag,
                    amount=str(transfer.amount),
                    amount_usd=float(transfer.amount_usd) if transfer.amount_usd else 0,
                    asset=transfer.currency,
                    contract_address=transfer.contract_address,
                    blockchain=transfer.blockchain,
                    currency=asset_model.symbol if asset_model else transfer.currency,
                    network=asset_model.network if asset_model else (transfer.network or ""),
                    source_vault_id=source_vault_id,
                    source_address=transfer.source_address,
                    fireblocks_asset_id=fireblocks_asset_id,
                )
                log.info(f"✅ Processed pending transfer: {transfer.request_id}")
        except Exception as e:
            log.error(f"❌ Failed to process pending transfer {transfer.id}: {e}")
