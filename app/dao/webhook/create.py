"""Create transaction from webhook."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import log
from app.enums.fireblocks import TransactionStatusEnum
from app.models import TransactionModel
from app.schemas.webhooks import TransactionDetailsSchema, WebhookProcessResultSchema

from .parse import parse_amount, parse_amount_usd
from .common import identify_wallet
from .update import update_wallet_balance, notify_backend_about_deposit


async def create_transaction(
    db: AsyncSession,
    tx: TransactionDetailsSchema,
    raw_body: str,
) -> WebhookProcessResultSchema:
    """Create new transaction record."""
    log.info(f"📝 Creating transaction record: tx_id={tx.id}")

    # Identify wallet (vault_id, wallet_id, asset_id)
    wallet_info = await identify_wallet(db, tx)

    # Parse amount
    amount = parse_amount(tx)

    # Create transaction record in DB
    transaction = TransactionModel(
        provider_tx_id=tx.id,
        tx_hash=tx.txHash,
        vault_id=wallet_info["vault_id"] if wallet_info else None,
        wallet_id=wallet_info["wallet_id"] if wallet_info else None,
        asset_id=wallet_info["asset_id"] if wallet_info else None,
        amount=amount,
        amount_usd=parse_amount_usd(tx),
        status=tx.status,
        num_confirmations=tx.numOfConfirmations,
        is_internal=False,  # Will be determined based on whitelist if needed
        source_address=tx.sourceAddress,
        destination_address=tx.destinationAddress,
        raw_webhook_data=raw_body,
    )

    db.add(transaction)
    await db.flush()

    # Всегда отправляем статус в backend; баланс обновляем только на COMPLETED
    if wallet_info:
        if tx.status == TransactionStatusEnum.COMPLETED.value:
            await update_wallet_balance(db, wallet_info, tx)
        await notify_backend_about_deposit(db, wallet_info, tx)

    log.info(
        f"✅ Transaction created: id={transaction.id}, tx_id={tx.id}, "
        f"amount={amount}, vault_id={wallet_info['vault_id'] if wallet_info else None}"
    )

    return WebhookProcessResultSchema(
        status="created",
        transaction_id=str(transaction.id),
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
