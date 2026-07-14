"""Webhook service - main processing logic."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import log
from app.models import (
    TransactionModel,
    AssetModel,
    TransferModel,
    WalletModel,
    VaultModel,
)
from app.enums.status import TransferStatus
from app.schemas.webhooks import (
    FireblocksWebhookPayloadSchema,
    TransactionDetailsSchema,
    WebhookProcessResultSchema,
)
from app.services.payout_callback import notify_backend_payout_status
from app.dao.transfer import complete_transfer_balance, release_reserve
from app.dao.transfer import process_pending_balance_transfer
from app.broker.publisher import publish_transfer_created

from .create import create_transaction
from .update import update_transaction, update_wallet_balance
from .common import identify_source_wallet, identify_wallet
from .parse import parse_amount, parse_amount_usd, parse_net_amount_decimal


async def process_webhook(
    db: AsyncSession,
    payload: FireblocksWebhookPayloadSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    log.info(
        f"📥 Webhook received: event={payload.eventType}, "
        f"id={payload.id}, resourceId={payload.resourceId}"
    )

    if not payload.eventType.startswith("transaction."):
        log.info(f"⏭️ Skipping event: {payload.eventType}")
        return WebhookProcessResultSchema(
            status="skipped", reason="not a transaction event"
        )

    tx = payload.get_transaction_details()
    if not tx:
        log.warning("⚠️ Failed to get transaction details")
        return WebhookProcessResultSchema(
            status="error", reason="failed to parse transaction details"
        )

    if payload.is_incoming_deposit():
        result = await _process_incoming_deposit(db, tx, raw_body)
        await db.commit()
        return result

    if payload.is_outgoing_withdrawal():
        result = await _process_outgoing_transfer(db, tx, payload, raw_body)
        await db.commit()
        return result

    # internal transfer между нашими vault (например на HOT)
    if payload.is_internal_transfer():
        result = await _process_internal_transfer(db, tx, raw_body)
        await db.commit()
        return result

    log.info(
        f"⏭️ Transaction is neither deposit nor withdrawal nor internal: "
        f"tx_id={tx.id}, operation={tx.operation}"
    )
    return WebhookProcessResultSchema(
        status="skipped", reason="not a deposit, withdrawal or internal transfer"
    )


async def _process_incoming_deposit(
    db: AsyncSession,
    tx: TransactionDetailsSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    log.info(
        f"💰 Processing incoming deposit: tx_id={tx.id}, "
        f"asset={tx.assetId}, status={tx.status}"
    )

    stmt = select(TransactionModel).where(TransactionModel.provider_tx_id == tx.id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        return await update_transaction(db, existing, tx, raw_body)
    else:
        return await create_transaction(db, tx, raw_body)


async def _process_outgoing_transfer(
    db: AsyncSession,
    tx: TransactionDetailsSchema,
    payload: FireblocksWebhookPayloadSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    # Ищем transfer по provider_tx_id или request_id (externalTxId), обновляем статус и баланс.
    log.info(
        f"📤 Processing outgoing transfer: tx_id={tx.id}, "
        f"asset={tx.assetId}, status={tx.status}"
    )

    request_id = tx.externalTxId

    transfer = await _find_transfer(db, tx.id, request_id)

    if not transfer:
        log.warning(
            f"⚠️ Transfer not found for webhook: provider_tx_id={tx.id}, "
            f"request_id={request_id}. Creating legacy transaction record."
        )
        # fallback в таблицу transactions для обратной совместимости
        return await _create_legacy_transaction(db, tx, raw_body)

    old_status = transfer.status

    if tx.txHash and not transfer.tx_hash:
        transfer.tx_hash = tx.txHash

    if payload.is_failed_or_rejected():
        await _handle_transfer_failed(db, transfer, tx)
    elif tx.status == "COMPLETED":
        await _handle_transfer_completed(db, transfer, tx)
    elif tx.status == "BROADCASTING":
        transfer.status = TransferStatus.BROADCASTING.value
    elif tx.status in ("PENDING_SIGNATURE", "QUEUED", "PENDING_AUTHORIZATION"):
        transfer.status = TransferStatus.SIGNING.value

    await db.flush()

    log.info(
        f"🔄 Updated transfer: {old_status} -> {transfer.status}, "
        f"request_id={transfer.request_id}"
    )

    return WebhookProcessResultSchema(
        status="processed",
        transaction_id=str(transfer.id),
        provider_tx_id=tx.id,
        amount=str(transfer.amount),
        vault_id=str(transfer.vault_id) if transfer.vault_id else None,
        wallet_id=str(transfer.wallet_id) if transfer.wallet_id else None,
        asset_id=str(transfer.asset_id) if transfer.asset_id else None,
    )


async def _find_transfer(
    db: AsyncSession,
    provider_tx_id: str,
    request_id: str | None,
) -> TransferModel | None:
    stmt = select(TransferModel).where(TransferModel.provider_tx_id == provider_tx_id)
    result = await db.execute(stmt)
    transfer = result.scalar_one_or_none()

    if transfer:
        return transfer

    if request_id:
        stmt = select(TransferModel).where(TransferModel.request_id == request_id)
        result = await db.execute(stmt)
        transfer = result.scalar_one_or_none()

        if transfer:
            # нашли по request_id - проставляем provider_tx_id
            transfer.provider_tx_id = provider_tx_id
            return transfer

    return None


async def _handle_transfer_failed(
    db: AsyncSession,
    transfer: TransferModel,
    tx: TransactionDetailsSchema,
) -> None:
    log.warning(
        f"❌ Transfer failed: provider_tx_id={tx.id}, status={tx.status}, "
        f"request_id={transfer.request_id}"
    )

    if transfer.wallet_id:
        await release_reserve(db, transfer.wallet_id, transfer.amount)

    error_msg = f"Transaction {tx.status}: {tx.subStatus or 'No details'}"
    transfer.status = TransferStatus.FAILED.value
    transfer.error_message = error_msg

    transfer.completed_at = datetime.now(timezone.utc)

    if not transfer.is_internal:
        await notify_backend_payout_status(
            str(transfer.request_id), "failed", tx_hash=tx.txHash
        )


async def _handle_transfer_completed(
    db: AsyncSession,
    transfer: TransferModel,
    tx: TransactionDetailsSchema,
) -> None:
    log.info(
        f"✅ Transfer completed: provider_tx_id={tx.id}, hash={tx.txHash}, "
        f"request_id={transfer.request_id}"
    )

    if transfer.wallet_id:
        await complete_transfer_balance(db, transfer.wallet_id, transfer.amount)

    transfer.status = TransferStatus.COMPLETED.value
    transfer.tx_hash = tx.txHash

    transfer.completed_at = datetime.now(timezone.utc)

    if not transfer.is_internal:
        await notify_backend_payout_status(
            str(transfer.request_id), "completed", tx_hash=tx.txHash
        )


async def _create_legacy_transaction(
    db: AsyncSession,
    tx: TransactionDetailsSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    # Когда transfer не найден в TransferModel - пишем в transactions (обратная совместимость).
    wallet_info = await identify_source_wallet(db, tx)
    amount = parse_amount(tx)
    amount_usd = parse_amount_usd(tx)

    stmt = select(TransactionModel).where(TransactionModel.provider_tx_id == tx.id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.status = tx.status
        existing.tx_hash = tx.txHash or existing.tx_hash
        existing.num_confirmations = tx.numOfConfirmations
        existing.raw_webhook_data = raw_body
    else:
        existing = TransactionModel(
            provider_tx_id=tx.id,
            request_id=tx.externalTxId,
            tx_hash=tx.txHash,
            vault_id=wallet_info["vault_id"] if wallet_info else None,
            wallet_id=wallet_info["wallet_id"] if wallet_info else None,
            asset_id=wallet_info["asset_id"] if wallet_info else None,
            amount=amount,
            amount_usd=amount_usd,
            status=tx.status,
            num_confirmations=tx.numOfConfirmations,
            is_internal=False,
            source_address=tx.sourceAddress,
            destination_address=tx.destinationAddress,
            raw_webhook_data=raw_body,
        )
        db.add(existing)
        await db.flush()

    return WebhookProcessResultSchema(
        status="processed",
        transaction_id=str(existing.id),
        provider_tx_id=tx.id,
        amount=str(amount),
        vault_id=(
            str(wallet_info["vault_id"])
            if wallet_info and wallet_info["vault_id"]
            else None
        ),
        wallet_id=(
            str(wallet_info["wallet_id"])
            if wallet_info and wallet_info["wallet_id"]
            else None
        ),
        asset_id=(
            str(wallet_info["asset_id"])
            if wallet_info and wallet_info["asset_id"]
            else None
        ),
    )


async def _process_internal_transfer(
    db: AsyncSession,
    tx: TransactionDetailsSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    # Перевод между нашими vault (treasury: HOT/WARM/COLD) - обновляем баланс src и dst.
    log.info(
        f"🔄 Processing internal transfer: tx_id={tx.id}, "
        f"asset={tx.assetId}, status={tx.status}"
    )

    if tx.status != "COMPLETED":
        log.info(f"⏳ Internal transfer not yet completed: status={tx.status}")
        return WebhookProcessResultSchema(
            status="pending",
            reason=f"waiting for completion, current status: {tx.status}",
            provider_tx_id=tx.id,
        )

    dest_wallet_info = await identify_wallet(db, tx)
    if not dest_wallet_info or not dest_wallet_info.get("wallet_id"):
        log.warning(
            f"⚠️ Destination wallet not found for internal transfer: "
            f"dest_vault={tx.destination.id if tx.destination else None}"
        )
        return WebhookProcessResultSchema(
            status="skipped",
            reason="destination wallet not found",
            provider_tx_id=tx.id,
        )

    await update_wallet_balance(db, dest_wallet_info, tx)

    source_wallet_info = await identify_source_wallet(db, tx)
    if source_wallet_info and source_wallet_info.get("wallet_id"):

        source_wallet = await db.get(WalletModel, source_wallet_info["wallet_id"])
        if source_wallet:
            amount = parse_net_amount_decimal(tx)
            new_balance = source_wallet.balance - amount
            if new_balance < 0:
                new_balance = 0  # не уходим в минус
            source_wallet.balance = new_balance
            log.info(
                f"💸 Decreased source wallet balance {source_wallet.id}: "
                f"amount={amount}, new_balance={new_balance}"
            )

    log.info(
        f"✅ Internal transfer processed: tx_id={tx.id}, "
        f"dest_wallet={dest_wallet_info.get('wallet_id')}"
    )

    # после пополнения - прогоняем очередь pending_balance
    await _process_pending_balance_queue(db, dest_wallet_info)

    return WebhookProcessResultSchema(
        status="processed",
        provider_tx_id=tx.id,
        amount=str(parse_amount(tx)) if parse_amount(tx) else None,
        vault_id=(
            str(dest_wallet_info["vault_id"])
            if dest_wallet_info.get("vault_id")
            else None
        ),
        wallet_id=(
            str(dest_wallet_info["wallet_id"])
            if dest_wallet_info.get("wallet_id")
            else None
        ),
        asset_id=(
            str(dest_wallet_info["asset_id"])
            if dest_wallet_info.get("asset_id")
            else None
        ),
    )


async def _process_pending_balance_queue(
    db: AsyncSession,
    wallet_info: dict,
) -> None:
    # Находим трансферы, ждущие этот asset, и пробуем зарезервировать баланс.
    asset_id = wallet_info.get("asset_id")
    if not asset_id:
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

    log.info(
        f"🔄 Processing {len(transfers)} pending_balance transfers for {asset.currency}"
    )

    for transfer in transfers:
        try:
            success = await process_pending_balance_transfer(db, transfer)
            if success:
                # source vault provider_id и fireblocks_asset_id для workflow
                source_vault_id = None
                fireblocks_asset_id = None
                if transfer.vault_id:
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
                    currency=asset_model.currency if asset_model else transfer.currency,
                    network=(
                        asset_model.network if asset_model else (transfer.network or "")
                    ),
                    source_vault_id=source_vault_id,
                    source_address=transfer.source_address,
                    fireblocks_asset_id=fireblocks_asset_id,
                )
                log.info(f"✅ Processed pending transfer: {transfer.request_id}")
        except Exception as e:
            log.error(f"❌ Failed to process pending transfer {transfer.id}: {e}")
