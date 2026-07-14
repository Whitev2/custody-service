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
    """
    Process webhook event.

    Args:
        db: Database session
        payload: Parsed webhook payload
        raw_body: Raw request body for storage

    Returns:
        Processing result
    """
    log.info(
        f"📥 Webhook received: event={payload.eventType}, "
        f"id={payload.id}, resourceId={payload.resourceId}"
    )

    # Process only transaction events
    if not payload.eventType.startswith("transaction."):
        log.info(f"⏭️ Skipping event: {payload.eventType}")
        return WebhookProcessResultSchema(
            status="skipped", reason="not a transaction event"
        )

    # Get transaction details
    tx = payload.get_transaction_details()
    if not tx:
        log.warning("⚠️ Failed to get transaction details")
        return WebhookProcessResultSchema(
            status="error", reason="failed to parse transaction details"
        )

    # Check if this is an incoming deposit to our vault
    if payload.is_incoming_deposit():
        result = await _process_incoming_deposit(db, tx, raw_body)
        await db.commit()
        return result

    # Check if this is an outgoing withdrawal from our vault
    if payload.is_outgoing_withdrawal():
        result = await _process_outgoing_transfer(db, tx, payload, raw_body)
        await db.commit()
        return result

    # Check if this is an internal transfer between our vaults (e.g., to HOT wallet)
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
    """Process incoming deposit (create/update transaction record)."""
    log.info(
        f"💰 Processing incoming deposit: tx_id={tx.id}, "
        f"asset={tx.assetId}, status={tx.status}"
    )

    # Check if transaction already exists in DB
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
    """
    Process outgoing transfer (payout/withdrawal) from webhook.

    Looks up transfer in TransferModel by provider_tx_id or request_id (externalTxId).
    Updates status and handles balance on completion/failure.
    """
    log.info(
        f"📤 Processing outgoing transfer: tx_id={tx.id}, "
        f"asset={tx.assetId}, status={tx.status}"
    )

    # Get request_id from externalTxId
    request_id = tx.externalTxId

    # Find transfer by provider_tx_id or request_id
    transfer = await _find_transfer(db, tx.id, request_id)

    if not transfer:
        log.warning(
            f"⚠️ Transfer not found for webhook: provider_tx_id={tx.id}, "
            f"request_id={request_id}. Creating legacy transaction record."
        )
        # Fallback: create in transactions table for backward compatibility
        return await _create_legacy_transaction(db, tx, raw_body)

    # Update transfer status
    old_status = transfer.status

    if tx.txHash and not transfer.tx_hash:
        transfer.tx_hash = tx.txHash

    # Handle terminal statuses
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
    """Find transfer by provider_tx_id or request_id."""
    # First try by provider_tx_id
    stmt = select(TransferModel).where(TransferModel.provider_tx_id == provider_tx_id)
    result = await db.execute(stmt)
    transfer = result.scalar_one_or_none()

    if transfer:
        return transfer

    # Then try by request_id
    if request_id:
        stmt = select(TransferModel).where(TransferModel.request_id == request_id)
        result = await db.execute(stmt)
        transfer = result.scalar_one_or_none()

        if transfer:
            # Update provider_tx_id if found by request_id
            transfer.provider_tx_id = provider_tx_id
            return transfer

    return None


async def _handle_transfer_failed(
    db: AsyncSession,
    transfer: TransferModel,
    tx: TransactionDetailsSchema,
) -> None:
    """
    Handle failed/rejected transfer.

    1. Release pending_amount (return reserved funds to available balance)
    2. Update status to FAILED
    3. Notify backend for external transfers (payouts)
    """
    log.warning(
        f"❌ Transfer failed: provider_tx_id={tx.id}, status={tx.status}, "
        f"request_id={transfer.request_id}"
    )

    # Release pending_amount if we have wallet_id
    if transfer.wallet_id:
        await release_reserve(db, transfer.wallet_id, transfer.amount)

    # Update status
    error_msg = f"Transaction {tx.status}: {tx.subStatus or 'No details'}"
    transfer.status = TransferStatus.FAILED.value
    transfer.error_message = error_msg

    transfer.completed_at = datetime.now(timezone.utc)

    # Notify backend for external transfers (payouts)
    if not transfer.is_internal:
        await notify_backend_payout_status(
            str(transfer.request_id), "failed", tx_hash=tx.txHash
        )


async def _handle_transfer_completed(
    db: AsyncSession,
    transfer: TransferModel,
    tx: TransactionDetailsSchema,
) -> None:
    """
    Handle completed transfer.

    1. Deduct both balance and pending_amount
    2. Update status to COMPLETED
    3. Notify backend for external transfers (payouts)
    """
    log.info(
        f"✅ Transfer completed: provider_tx_id={tx.id}, hash={tx.txHash}, "
        f"request_id={transfer.request_id}"
    )

    # Deduct from balance and pending_amount
    if transfer.wallet_id:
        await complete_transfer_balance(db, transfer.wallet_id, transfer.amount)

    # Update status
    transfer.status = TransferStatus.COMPLETED.value
    transfer.tx_hash = tx.txHash

    transfer.completed_at = datetime.now(timezone.utc)

    # Notify backend for external transfers (payouts)
    if not transfer.is_internal:
        await notify_backend_payout_status(
            str(transfer.request_id), "completed", tx_hash=tx.txHash
        )


async def _create_legacy_transaction(
    db: AsyncSession,
    tx: TransactionDetailsSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    """
    Create legacy transaction record for backward compatibility.

    Used when transfer is not found in TransferModel table.
    """
    wallet_info = await identify_source_wallet(db, tx)
    amount = parse_amount(tx)
    amount_usd = parse_amount_usd(tx)

    # Check if already exists in transactions table
    stmt = select(TransactionModel).where(TransactionModel.provider_tx_id == tx.id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing
        existing.status = tx.status
        existing.tx_hash = tx.txHash or existing.tx_hash
        existing.num_confirmations = tx.numOfConfirmations
        existing.raw_webhook_data = raw_body
    else:
        # Create new
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
    """
    Process internal transfer between our vaults.

    Updates balances on both source and destination wallets.
    Used for treasury management (HOT/WARM/COLD transfers).
    """
    log.info(
        f"🔄 Processing internal transfer: tx_id={tx.id}, "
        f"asset={tx.assetId}, status={tx.status}"
    )

    # Only update balances on COMPLETED status
    if tx.status != "COMPLETED":
        log.info(f"⏳ Internal transfer not yet completed: status={tx.status}")
        return WebhookProcessResultSchema(
            status="pending",
            reason=f"waiting for completion, current status: {tx.status}",
            provider_tx_id=tx.id,
        )

    # Identify destination wallet (where funds are going)
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

    # Update destination wallet balance (increase)
    await update_wallet_balance(db, dest_wallet_info, tx)

    # Identify source wallet (where funds are coming from)
    source_wallet_info = await identify_source_wallet(db, tx)
    if source_wallet_info and source_wallet_info.get("wallet_id"):

        source_wallet = await db.get(WalletModel, source_wallet_info["wallet_id"])
        if source_wallet:
            amount = parse_net_amount_decimal(tx)
            new_balance = source_wallet.balance - amount
            if new_balance < 0:
                new_balance = 0  # Safety: don't go negative
            source_wallet.balance = new_balance
            log.info(
                f"💸 Decreased source wallet balance {source_wallet.id}: "
                f"amount={amount}, new_balance={new_balance}"
            )

    log.info(
        f"✅ Internal transfer processed: tx_id={tx.id}, "
        f"dest_wallet={dest_wallet_info.get('wallet_id')}"
    )

    # Process pending_balance queue after balance update
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
    """
    Process pending_balance transfers after balance update.

    Finds transfers waiting for this asset and tries to reserve balance.
    """

    asset_id = wallet_info.get("asset_id")
    if not asset_id:
        return

    # Get asset info for filtering
    asset = await db.get(AssetModel, asset_id)
    if not asset:
        return

    # Find pending_balance transfers for this asset
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
                # Get source vault provider_id and fireblocks_asset_id for workflow
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

                # Publish to workflow
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
