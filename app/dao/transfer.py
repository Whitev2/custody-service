"""Transfer DAO - database operations for outgoing transfers."""

from decimal import Decimal
from uuid import UUID, uuid4
from datetime import datetime, timezone

from sqlalchemy import select, update, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    WalletModel,
    VaultModel,
    AssetModel,
    TransferModel
)
from app.enums.status import TransferStatus
from app.enums.types import VaultTypeEnum
from app.services.payout_callback import notify_backend_payout_status
from app.config import log


class InsufficientBalanceError(Exception):
    """Not enough balance in HOT wallet."""

    pass


class NoHotWalletError(Exception):
    """No suitable HOT wallet found."""

    pass


class TransferNotFoundError(Exception):
    """Transfer not found."""

    pass


# ============================================================================
# Balance Reservation Functions
# ============================================================================


async def select_and_reserve_hot_wallet(
    db: AsyncSession,
    contract_address: str | None,
    blockchain: str,
    amount: Decimal,
) -> tuple[WalletModel, VaultModel, AssetModel]:
    """
    Atomically select HOT wallet AND reserve balance in one SQL query.

    This is safe for concurrent access from multiple pods - PostgreSQL
    serializes concurrent UPDATEs on the same row automatically.

    Args:
        db: Database session
        contract_address: Token contract address (None for native tokens)
        blockchain: Blockchain name (ETHEREUM, TRON, BSC)
        amount: Required amount

    Returns:
        Tuple of (wallet, vault, asset)

    Raises:
        NoHotWalletError: No HOT wallet found for this asset
        InsufficientBalanceError: HOT wallet balance too low
    """
    asset_desc = contract_address or f"native on {blockchain}"

    # Single atomic UPDATE that selects best wallet and reserves balance
    # PostgreSQL handles concurrent access automatically
    # Note: blockchain comparison is case-insensitive (UPPER)
    # Note: COALESCE handles NULL pending_amount/balance
    sql = text(
        """
        UPDATE wallets w
        SET pending_amount = COALESCE(w.pending_amount, 0) + :amount
        FROM vaults v, assets a
        WHERE w.vault_id = v.id
          AND w.asset_id = a.id
          AND v.vault_type = :vault_type
          AND v.is_active = true
          AND UPPER(a.blockchain) = UPPER(:blockchain)
          AND a.contract_address IS NOT DISTINCT FROM :contract_address
          AND (COALESCE(w.balance, 0) - COALESCE(w.pending_amount, 0)) >= :amount
          AND w.id = (
              SELECT w2.id 
              FROM wallets w2
              JOIN vaults v2 ON w2.vault_id = v2.id
              JOIN assets a2 ON w2.asset_id = a2.id
              WHERE v2.vault_type = :vault_type
                AND v2.is_active = true
                AND UPPER(a2.blockchain) = UPPER(:blockchain)
                AND a2.contract_address IS NOT DISTINCT FROM :contract_address
                AND (COALESCE(w2.balance, 0) - COALESCE(w2.pending_amount, 0)) >= :amount
              ORDER BY v2.is_primary DESC, (COALESCE(w2.balance, 0) - COALESCE(w2.pending_amount, 0)) DESC
              LIMIT 1
          )
        RETURNING w.id, w.vault_id, w.asset_id, w.address, 
                  w.balance, w.pending_amount,
                  v.provider_vault_id, v.name as vault_name,
                  a.symbol as currency, a.contract_address as asset_contract
    """
    )

    result = await db.execute(
        sql,
        {
            "amount": amount,
            "blockchain": blockchain,
            "contract_address": contract_address,
            "vault_type": VaultTypeEnum.HOT.value,
        },
    )

    row = result.fetchone()

    if row is None:
        # Check why: no wallet exists or insufficient balance?
        check_sql = text(
            """
            SELECT w.id, w.balance, w.pending_amount, 
                   (COALESCE(w.balance, 0) - COALESCE(w.pending_amount, 0)) as available
            FROM wallets w
            JOIN vaults v ON w.vault_id = v.id
            JOIN assets a ON w.asset_id = a.id
            WHERE v.vault_type = :vault_type
              AND v.is_active = true
              AND UPPER(a.blockchain) = UPPER(:blockchain)
              AND a.contract_address IS NOT DISTINCT FROM :contract_address
            ORDER BY (COALESCE(w.balance, 0) - COALESCE(w.pending_amount, 0)) DESC
            LIMIT 1
        """
        )

        check_result = await db.execute(
            check_sql,
            {
                "blockchain": blockchain,
                "contract_address": contract_address,
                "vault_type": VaultTypeEnum.HOT.value,
            },
        )
        check_row = check_result.fetchone()

        # Debug logging
        log.debug(
            f"HOT wallet check: blockchain={blockchain}, contract={contract_address}, "
            f"vault_type={VaultTypeEnum.HOT.value}"
        )

        if check_row is None:
            # Additional debug - check without contract_address filter
            debug_sql = text(
                """
                SELECT a.blockchain, a.contract_address, a.symbol, w.balance, 
                       COALESCE(w.balance, 0) as bal, COALESCE(w.pending_amount, 0) as pend
                FROM wallets w
                JOIN vaults v ON w.vault_id = v.id
                JOIN assets a ON w.asset_id = a.id
                WHERE v.vault_type = :vault_type AND v.is_active = true
            """
            )
            debug_result = await db.execute(
                debug_sql, {"vault_type": VaultTypeEnum.HOT.value}
            )
            debug_rows = debug_result.fetchall()
            for dr in debug_rows:
                log.warning(
                    f"DEBUG HOT wallet: blockchain={dr.blockchain}, contract={dr.contract_address}, "
                    f"symbol={dr.symbol}, balance={dr.balance}, bal={dr.bal}, pend={dr.pend}"
                )
            raise NoHotWalletError(f"No HOT wallet found for {asset_desc}")
        else:
            log.debug(
                f"HOT wallet found but insufficient: balance={check_row.balance}, "
                f"pending={check_row.pending_amount}, available={check_row.available}"
            )
            raise InsufficientBalanceError(
                f"Insufficient HOT balance for {asset_desc}. "
                f"Required: {amount}, Available: {check_row.available}"
            )

    log.info(
        f"Selected and reserved HOT wallet: vault={row.vault_name}, "
        f"address={row.address}, reserved={amount}"
    )

    # Load full ORM objects for response
    wallet = await db.get(
        WalletModel,
        row.id,
        options=[selectinload(WalletModel.vault), selectinload(WalletModel.asset)],
    )

    return wallet, wallet.vault, wallet.asset


async def reserve_balance(
    db: AsyncSession,
    wallet_id: UUID,
    amount: Decimal,
) -> bool:
    """
    Reserve additional balance on wallet.

    Note: For new transfers, use select_and_reserve_hot_wallet() instead.
    This is kept for edge cases.
    """
    stmt = (
        update(WalletModel)
        .where(
            WalletModel.id == wallet_id,
            (WalletModel.balance - WalletModel.pending_amount) >= amount,
        )
        .values(pending_amount=WalletModel.pending_amount + amount)
        .returning(WalletModel.id)
    )

    result = await db.execute(stmt)
    updated = result.scalar_one_or_none()

    if updated:
        log.info(f"Reserved {amount} on wallet {wallet_id}")
        return True

    log.warning(f"Failed to reserve {amount} on wallet {wallet_id}")
    return False


async def release_reserve(
    db: AsyncSession,
    wallet_id: UUID,
    amount: Decimal,
) -> bool:
    """
    Release reserved balance (on reject/cancel).
    """
    stmt = (
        update(WalletModel)
        .where(
            WalletModel.id == wallet_id,
            WalletModel.pending_amount >= amount,
        )
        .values(pending_amount=WalletModel.pending_amount - amount)
        .returning(WalletModel.id)
    )

    result = await db.execute(stmt)
    updated = result.scalar_one_or_none()

    if updated:
        log.info(f"Released reserve {amount} on wallet {wallet_id}")
        return True

    log.warning(f"Failed to release reserve {amount} on wallet {wallet_id}")
    return False


async def complete_transfer_balance(
    db: AsyncSession,
    wallet_id: UUID,
    amount: Decimal,
) -> bool:
    """
    Complete transfer: release reserve and deduct from balance.

    Called when transaction is confirmed on blockchain.
    """
    stmt = (
        update(WalletModel)
        .where(
            WalletModel.id == wallet_id,
            WalletModel.pending_amount >= amount,
            WalletModel.balance >= amount,
        )
        .values(
            pending_amount=WalletModel.pending_amount - amount,
            balance=WalletModel.balance - amount,
        )
        .returning(WalletModel.id)
    )

    result = await db.execute(stmt)
    updated = result.scalar_one_or_none()

    if updated:
        log.info(f"Completed transfer: deducted {amount} from wallet {wallet_id}")
        return True

    log.warning(f"Failed to complete transfer balance for wallet {wallet_id}")
    return False


# ============================================================================
# Transfer CRUD Functions
# ============================================================================


async def create_transfer(
    db: AsyncSession,
    request_id: str,
    blockchain: str,
    currency: str,
    destination_address: str,
    amount: Decimal,
    contract_address: str | None,
    amount_usd: Decimal,
    is_internal: bool = False,
    destination_tag: str | None = None,
    note: str | None = None,
    # For internal transfers with known source
    vault_id: UUID | None = None,
    wallet_id: UUID | None = None,
    asset_id: UUID | None = None,
    source_address: str | None = None,
    to_vault_id: UUID | None = None,
    # Initial status
    status: TransferStatus = TransferStatus.PENDING_APPROVAL,
) -> TransferModel:
    """
    Create a new transfer record.

    For external transfers: starts with PENDING_APPROVAL
    For internal transfers: starts with PENDING (balance already reserved)
    """
    transfer = TransferModel(
        id=uuid4(),
        request_id=request_id,
        is_internal=is_internal,
        vault_id=vault_id,
        wallet_id=wallet_id,
        asset_id=asset_id,
        source_address=source_address,
        currency=currency,
        contract_address=contract_address,
        blockchain=blockchain,
        destination_address=destination_address,
        destination_tag=destination_tag,
        to_vault_id=to_vault_id,
        amount=amount,
        amount_usd=amount_usd,
        status=status.value,
        note=note,
        reserved_at=datetime.now(timezone.utc) if wallet_id else None,
    )

    db.add(transfer)
    await db.flush()

    log.info(
        f"Created transfer: id={transfer.id}, request_id={request_id}, "
        f"is_internal={is_internal}, amount={amount}, destination={destination_address[:20]}..."
    )

    return transfer


async def get_transfer_by_request_id(
    db: AsyncSession,
    request_id: str,
) -> TransferModel | None:
    """Get transfer by request_id with relationships loaded."""
    stmt = (
        select(TransferModel)
        .where(TransferModel.request_id == request_id)
        .options(
            selectinload(TransferModel.wallet),
            selectinload(TransferModel.vault),
            selectinload(TransferModel.asset),
            selectinload(TransferModel.to_vault),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_transfer_by_id(
    db: AsyncSession,
    transfer_id: UUID,
) -> TransferModel | None:
    """Get transfer by ID with relationships loaded."""
    stmt = (
        select(TransferModel)
        .where(TransferModel.id == transfer_id)
        .options(
            selectinload(TransferModel.wallet),
            selectinload(TransferModel.vault),
            selectinload(TransferModel.asset),
            selectinload(TransferModel.to_vault),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_transfer_status(
    db: AsyncSession,
    request_id: str,
    status: TransferStatus,
    provider_tx_id: str | None = None,
    tx_hash: str | None = None,
    error_message: str | None = None,
) -> TransferModel | None:
    """
    Update transfer status and related fields.
    """
    transfer = await get_transfer_by_request_id(db, request_id)
    if not transfer:
        return None

    transfer.status = status.value

    if provider_tx_id:
        transfer.provider_tx_id = provider_tx_id
    if tx_hash:
        transfer.tx_hash = tx_hash
    if error_message:
        transfer.error_message = error_message

    # Update timestamps based on status
    now = datetime.now(timezone.utc)
    if status in (
        TransferStatus.COMPLETED,
        TransferStatus.REJECTED,
        TransferStatus.FAILED,
        TransferStatus.CANCELLED,
    ):
        transfer.completed_at = now

    await db.flush()

    log.info(f"Updated transfer {request_id} status to {status.value}")
    return transfer


async def update_transfer_with_wallet(
    db: AsyncSession,
    transfer: TransferModel,
    wallet: WalletModel,
    vault: VaultModel,
    asset: AssetModel,
) -> TransferModel:
    """
    Update transfer with selected wallet info after balance reservation.
    """
    transfer.vault_id = vault.id
    transfer.wallet_id = wallet.id
    transfer.asset_id = asset.id
    transfer.source_address = wallet.address
    transfer.status = TransferStatus.PENDING.value
    transfer.reserved_at = datetime.now(timezone.utc)

    await db.flush()

    log.info(
        f"Updated transfer {transfer.request_id} with wallet: "
        f"address={wallet.address}, vault={vault.name}"
    )

    return transfer


# ============================================================================
# Pending Balance Queue Functions
# ============================================================================


async def get_pending_balance_transfers(
    db: AsyncSession,
    limit: int = 100,
) -> list[TransferModel]:
    """
    Get transfers waiting for balance, ordered by creation time (FIFO).
    """
    stmt = (
        select(TransferModel)
        .where(TransferModel.status == TransferStatus.PENDING_BALANCE.value)
        .order_by(TransferModel.created_at.asc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_pending_balance_transfers_grouped(
    db: AsyncSession,
    limit_per_group: int = 50,
) -> dict[tuple[str | None, str], list[TransferModel]]:
    """
    Get pending_balance transfers grouped by (contract_address, blockchain).
    FIFO order within each group.

    Returns: {(contract_address, blockchain): [transfer1, transfer2, ...]}
    """
    stmt = (
        select(TransferModel)
        .where(TransferModel.status == TransferStatus.PENDING_BALANCE.value)
        .order_by(TransferModel.created_at.asc())
    )

    result = await db.execute(stmt)
    transfers = list(result.scalars().all())

    # Group by (contract_address, blockchain)
    grouped: dict[tuple[str | None, str], list[TransferModel]] = {}
    for transfer in transfers:
        key = (transfer.contract_address, transfer.blockchain)
        if key not in grouped:
            grouped[key] = []
        if len(grouped[key]) < limit_per_group:
            grouped[key].append(transfer)

    return grouped


async def get_hot_wallets_available_balance(
    db: AsyncSession,
) -> dict[tuple[str | None, str], Decimal]:
    """
    Get available balance for all HOT wallets, grouped by (contract_address, blockchain).

    Returns: {(contract_address, blockchain): total_available_balance}
    """
    sql = text(
        """
        SELECT 
            a.contract_address,
            a.blockchain,
            SUM(w.balance - w.pending_amount) as available
        FROM wallets w
        JOIN vaults v ON w.vault_id = v.id
        JOIN assets a ON w.asset_id = a.id
        WHERE v.vault_type = :vault_type
          AND v.is_active = true
          AND (w.balance - w.pending_amount) > 0
        GROUP BY a.contract_address, a.blockchain
    """
    )

    result = await db.execute(sql, {"vault_type": VaultTypeEnum.HOT.value})
    rows = result.fetchall()

    return {
        (row.contract_address, row.blockchain): Decimal(str(row.available))
        for row in rows
    }


async def process_pending_balance_transfer(
    db: AsyncSession,
    transfer: TransferModel,
) -> bool:
    """
    Try to process a pending_balance transfer.

    Returns True if successfully reserved balance and ready for signing.
    Returns False if still insufficient balance.
    """
    try:
        # Try to reserve balance atomically
        wallet, vault, asset = await select_and_reserve_hot_wallet(
            db=db,
            contract_address=transfer.contract_address,
            blockchain=transfer.blockchain,
            amount=transfer.amount,
        )

        # Success! Update transfer with wallet info
        await update_transfer_with_wallet(db, transfer, wallet, vault, asset)

        log.info(
            f"Processed pending_balance transfer: id={transfer.id}, "
            f"wallet={wallet.address}, amount={transfer.amount}"
        )

        return True

    except (InsufficientBalanceError, NoHotWalletError) as e:
        # Still no balance - increment retry count
        transfer.retry_count += 1
        await db.flush()

        log.debug(
            f"Pending transfer still waiting: id={transfer.id}, "
            f"retry_count={transfer.retry_count}, reason={e}"
        )

        return False


async def get_pending_balance_queue_stats(db: AsyncSession) -> dict:
    """
    Get statistics for pending_balance queue.
    """

    stmt = select(
        func.count(TransferModel.id).label("count"),
        func.sum(TransferModel.amount).label("total_amount"),
        func.min(TransferModel.created_at).label("oldest"),
    ).where(TransferModel.status == TransferStatus.PENDING_BALANCE.value)

    result = await db.execute(stmt)
    row = result.one()

    return {
        "pending_count": row.count or 0,
        "total_amount": float(row.total_amount) if row.total_amount else 0,
        "oldest_created_at": row.oldest.isoformat() if row.oldest else None,
    }


# ============================================================================
# Cancel Functions
# ============================================================================


async def cancel_transfer(
    db: AsyncSession,
    transfer: TransferModel,
    reason: str,
) -> TransferModel:
    """
    Cancel a transfer (only pending_approval, pending_balance or pending).

    If transfer has reserved balance, releases it.
    Notifies backend about cancellation for external transfers.
    """
    if not transfer.is_cancellable:
        raise ValueError(f"Cannot cancel transfer in status {transfer.status}")

    # Release reserve if balance was reserved
    if transfer.status == TransferStatus.PENDING.value and transfer.wallet_id:
        await release_reserve(db, transfer.wallet_id, transfer.amount)

    transfer.status = TransferStatus.CANCELLED.value
    transfer.error_message = f"Cancelled: {reason}"
    transfer.completed_at = datetime.now(timezone.utc)

    await db.flush()

    log.info(
        f"Cancelled transfer: id={transfer.id}, request_id={transfer.request_id}, "
        f"reason={reason}"
    )

    # Notify backend for external transfers (payouts)
    if not transfer.is_internal:
        await notify_backend_payout_status(str(transfer.request_id), "cancelled")

    return transfer
