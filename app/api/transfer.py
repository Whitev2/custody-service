"""Transfer API endpoints - Approval First flow."""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.transfer import (
    InternalTransferRequest,
    ExternalTransferRequest,
    TransferResponse,
    ApproveResponse,
    RejectRequest,
    CompleteRequest,
    SigningRequest,
    CancelRequest,
    QueueStatsResponse,
)
from app.models import VaultModel, AssetModel, WalletModel
from app.models.transfer import TransferModel
from app.enums.status import TransferStatus
from app.dao.transfer import (
    create_transfer,
    get_transfer_by_request_id,
    update_transfer_status,
    update_transfer_with_wallet,
    select_and_reserve_hot_wallet,
    release_reserve,
    complete_transfer_balance,
    cancel_transfer,
    get_pending_balance_queue_stats,
    InsufficientBalanceError,
    NoHotWalletError,
)
from app.broker.publisher import publish_transfer_created, publish_balance_ready
from app.services.custody import get_provider
from app.services.treasury_bootstrap import ensure_asset_in_hot_wallet
from app.storage.database import get_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/transfer", tags=["Transfer"])


# ============================================================================
# Helper Functions
# ============================================================================


def _transfer_to_response(transfer: TransferModel) -> TransferResponse:
    """Convert TransferModel to TransferResponse."""
    return TransferResponse(
        transfer_id=transfer.id,
        request_id=transfer.request_id,
        status=transfer.status,
        is_internal=transfer.is_internal,
        source_vault_id=(
            str(transfer.vault.provider_vault_id) if transfer.vault else None
        ),
        source_address=transfer.source_address,
        destination_address=transfer.destination_address,
        destination_tag=transfer.destination_tag,
        to_vault_id=str(transfer.to_vault_id) if transfer.to_vault_id else None,
        amount=str(transfer.amount),
        amount_usd=transfer.amount_usd,
        asset=transfer.currency,
        blockchain=transfer.blockchain,
        contract_address=transfer.contract_address,
        provider_tx_id=transfer.provider_tx_id,
        tx_hash=transfer.tx_hash,
        error_message=transfer.error_message,
        created_at=transfer.created_at,
    )


# ============================================================================
# External Transfer Endpoints (Approval First Flow)
# ============================================================================


@router.post(
    "/external/create",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create external transfer (requires Workflow approval)",
)
async def create_external_transfer(
    request: ExternalTransferRequest,
    db: AsyncSession = Depends(get_db),
) -> TransferResponse:
    """
    Create external transfer with Upfront Reserve flow.

    1. Reserves balance on HOT wallet FIRST
    2. If balance OK: status=PENDING_APPROVAL, publishes transfer.created
    3. If no balance: status=PENDING_BALANCE, waits in queue (no publish)

    Workflow will consume the event, do AML/Policy check, then publish approve/reject.
    """
    log.info(
        "Creating external transfer",
        extra={
            "request_id": request.request_id,
            "amount": request.amount,
            "blockchain": request.blockchain,
            "destination": request.to_address[:20] + "...",
        },
    )

    try:
        # Check if transfer already exists
        existing = await get_transfer_by_request_id(db, request.request_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Transfer with request_id '{request.request_id}' already exists",
            )

        # Try to reserve balance on HOT wallet FIRST
        source_vault_id = None
        source_address = None
        wallet = None
        vault = None
        asset = None
        balance_reserved = False

        try:
            wallet, vault, asset = await select_and_reserve_hot_wallet(
                db=db,
                contract_address=request.contract_address,
                blockchain=request.blockchain,
                amount=Decimal(request.amount),
            )
            source_vault_id = vault.provider_vault_id
            source_address = wallet.address
            balance_reserved = True

            log.info(
                "Balance reserved for transfer",
                extra={
                    "request_id": request.request_id,
                    "source_vault_id": source_vault_id,
                    "source_address": source_address,
                    "amount": request.amount,
                },
            )
        except NoHotWalletError as e:
            # Пробуем автоматически активировать ассет в HOT кошельке
            log.info(
                f"No HOT wallet for asset, trying to auto-activate: {e}",
                extra={"request_id": request.request_id},
            )
            
            asset_activated = await ensure_asset_in_hot_wallet(
                db=db,
                blockchain=request.blockchain,
                contract_address=request.contract_address,
            )
            
            if asset_activated:
                # Повторяем попытку резервирования после активации
                try:
                    wallet, vault, asset = await select_and_reserve_hot_wallet(
                        db=db,
                        contract_address=request.contract_address,
                        blockchain=request.blockchain,
                        amount=Decimal(request.amount),
                    )
                    source_vault_id = vault.provider_vault_id
                    source_address = wallet.address
                    balance_reserved = True
                    
                    log.info(
                        "Balance reserved after auto-activation",
                        extra={
                            "request_id": request.request_id,
                            "source_vault_id": source_vault_id,
                        },
                    )
                except (InsufficientBalanceError, NoHotWalletError) as retry_e:
                    log.warning(
                        f"Still no balance after activation: {retry_e}",
                        extra={"request_id": request.request_id},
                    )
                    balance_reserved = False
            else:
                log.warning(
                    f"Failed to auto-activate asset, transfer will wait: {e}",
                    extra={"request_id": request.request_id},
                )
                balance_reserved = False
                
        except InsufficientBalanceError as e:
            log.warning(
                f"Insufficient balance, transfer will wait in queue: {e}",
                extra={"request_id": request.request_id},
            )
            balance_reserved = False

        # Create transfer with appropriate status
        transfer_status = (
            TransferStatus.PENDING_APPROVAL
            if balance_reserved
            else TransferStatus.PENDING_BALANCE
        )

        transfer = await create_transfer(
            db=db,
            request_id=request.request_id,
            blockchain=request.blockchain,
            currency=request.asset,
            destination_address=request.to_address,
            amount=Decimal(request.amount),
            contract_address=request.contract_address,
            amount_usd=request.amount_usd,
            is_internal=False,
            destination_tag=request.destination_tag,
            note=request.note,
            status=transfer_status,
        )

        # If balance reserved, update transfer with wallet info
        if balance_reserved and wallet and vault and asset:
            transfer.vault_id = vault.id
            transfer.wallet_id = wallet.id
            transfer.asset_id = asset.id
            transfer.source_address = source_address
            transfer.reserved_at = datetime.now(timezone.utc)

        await db.commit()

        # Only publish to workflow if balance is reserved
        if balance_reserved:
            published = await publish_transfer_created(
                request_id=request.request_id,
                destination_address=request.to_address,
                destination_tag=request.destination_tag,
                amount=request.amount,
                amount_usd=float(request.amount_usd),
                asset=request.asset,
                contract_address=request.contract_address,
                blockchain=request.blockchain,
                currency=asset.currency if asset else request.asset,
                network=asset.network if asset else "",
                source_vault_id=source_vault_id,
                source_address=source_address,
                fireblocks_asset_id=asset.asset if asset else None,
            )

            if not published:
                log.warning(
                    "Failed to publish transfer.created event, but transfer was saved",
                    extra={"request_id": request.request_id},
                )

            log.info(
                "External transfer created with PENDING_APPROVAL status",
                extra={
                    "request_id": request.request_id,
                    "transfer_id": str(transfer.id),
                },
            )
        else:
            log.info(
                "External transfer created with PENDING_BALANCE status (waiting for funds)",
                extra={
                    "request_id": request.request_id,
                    "transfer_id": str(transfer.id),
                },
            )

        # Reload with relationships
        transfer = await get_transfer_by_request_id(db, request.request_id)

        return _transfer_to_response(transfer)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        log.error(f"Failed to create external transfer: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create transfer: {str(e)}",
        )


# ============================================================================
# Internal Transfer Endpoints (No Approval Required)
# ============================================================================


@router.post(
    "/internal/create",
    response_model=TransferResponse,
    summary="Create internal transfer (whitelist only, no approval)",
)
async def create_internal_transfer(
    request: InternalTransferRequest,
    db: AsyncSession = Depends(get_db),
) -> TransferResponse:
    """
    Create internal transfer (whitelist only).

    1. Validates source vault and wallet
    2. Checks whitelist if to_address is provided
    3. Reserves balance immediately
    4. Creates transaction in Fireblocks
    5. Returns transfer with provider_tx_id
    """
    log.info(
        "Creating internal transfer",
        extra={
            "request_id": request.request_id,
            "from_vault_id": str(request.from_vault_id),
            "amount": request.amount,
        },
    )

    # Check if transfer already exists
    existing = await get_transfer_by_request_id(db, request.request_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Transfer with request_id '{request.request_id}' already exists",
        )

    # Get source vault
    from_vault = await db.get(VaultModel, request.from_vault_id)
    if not from_vault:
        raise HTTPException(status_code=404, detail="Source vault not found")

    # Get asset
    asset = await db.get(AssetModel, request.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Get source wallet
    stmt = select(WalletModel).where(
        WalletModel.vault_id == request.from_vault_id,
        WalletModel.asset_id == request.asset_id,
    )
    result = await db.execute(stmt)
    from_wallet = result.scalar_one_or_none()
    if not from_wallet:
        raise HTTPException(status_code=404, detail="Source wallet not found")

    # Check balance
    amount = Decimal(request.amount)
    if not from_wallet.has_sufficient_balance(amount):
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Available: {from_wallet.available_balance}, Required: {amount}",
        )

    # Determine destination
    to_address = None
    to_vault_id = None

    if request.to_vault_id:
        to_vault = await db.get(VaultModel, request.to_vault_id)
        if not to_vault:
            raise HTTPException(status_code=404, detail="Destination vault not found")

        # Get destination wallet address
        stmt = select(WalletModel).where(
            WalletModel.vault_id == request.to_vault_id,
            WalletModel.asset_id == request.asset_id,
        )
        result = await db.execute(stmt)
        to_wallet = result.scalar_one_or_none()
        if not to_wallet:
            raise HTTPException(status_code=404, detail="Destination wallet not found")

        to_address = to_wallet.address
        to_vault_id = request.to_vault_id

    elif request.to_address:
        to_address = request.to_address

        # Check whitelist
        provider = get_provider()
        whitelist = await provider.get_whitelist_addresses(
            from_vault.provider_vault_id, asset.asset
        )
        whitelist_entry = next(
            (addr for addr in whitelist if addr.get("address") == to_address), None
        )
        if not whitelist_entry:
            raise HTTPException(
                status_code=400, detail="Destination address not in whitelist"
            )
    else:
        raise HTTPException(
            status_code=400, detail="Either to_vault_id or to_address must be provided"
        )

    try:
        # Reserve balance
        from app.dao.transfer import reserve_balance

        reserved = await reserve_balance(db, from_wallet.id, amount)
        if not reserved:
            raise HTTPException(
                status_code=400,
                detail="Failed to reserve balance (concurrent modification)",
            )

        # Create transaction in Fireblocks
        provider = get_provider()

        if to_vault_id:
            # Use destination vault
            to_vault = await db.get(VaultModel, to_vault_id)
            destination = {"type": "VAULT_ACCOUNT", "id": to_vault.provider_vault_id}
        else:
            # Use whitelist address
            destination = {
                "type": "ONE_TIME_ADDRESS",
                "oneTimeAddress": {"address": to_address},
            }

        tx_data = {
            "assetId": asset.asset,
            "source": {"type": "VAULT_ACCOUNT", "id": from_vault.provider_vault_id},
            "destination": destination,
            "amount": request.amount,
            "externalTxId": request.request_id,
        }
        if request.note:
            tx_data["note"] = request.note

        fb_tx = await provider.create_transaction(tx_data)

        # Create transfer record
        transfer = await create_transfer(
            db=db,
            request_id=request.request_id,
            blockchain=request.blockchain,
            currency=request.asset,
            destination_address=to_address,
            amount=amount,
            contract_address=request.contract_address,
            amount_usd=request.amount_usd,
            is_internal=True,
            destination_tag=request.destination_tag,
            note=request.note,
            vault_id=from_vault.id,
            wallet_id=from_wallet.id,
            asset_id=None,  # Will be resolved later if needed
            source_address=from_wallet.address,
            to_vault_id=to_vault_id,
            status=TransferStatus.SIGNING,
        )

        # Update with Fireblocks transaction ID
        transfer.provider_tx_id = fb_tx["id"]
        transfer.tx_hash = fb_tx.get("txHash")

        await db.commit()

        # Reload with relationships
        transfer = await get_transfer_by_request_id(db, request.request_id)

        log.info(
            "Internal transfer created and sent to Fireblocks",
            extra={
                "request_id": request.request_id,
                "transfer_id": str(transfer.id),
                "provider_tx_id": fb_tx["id"],
            },
        )

        return _transfer_to_response(transfer)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        log.error(f"Failed to create internal transfer: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create transfer: {str(e)}",
        )


# ============================================================================
# Transfer Lifecycle Endpoints
# ============================================================================


@router.get(
    "/{request_id}",
    response_model=TransferResponse,
    summary="Get transfer status",
)
async def get_transfer_status(
    request_id: str,
    db: AsyncSession = Depends(get_db),
) -> TransferResponse:
    """Get transfer status by request_id."""
    transfer = await get_transfer_by_request_id(db, request_id)

    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transfer not found: {request_id}",
        )

    return _transfer_to_response(transfer)


@router.post(
    "/{request_id}/approve",
    response_model=ApproveResponse,
    summary="Approve transfer (reserves balance)",
)
async def approve_transfer(
    request_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApproveResponse:
    """
    Approve transfer and reserve balance on HOT wallet.

    Called by Workflow after AML/Policy approval.

    1. Reserves balance on best available HOT wallet
    2. If balance available: status=PENDING, publishes balance_ready
    3. If insufficient balance: status=PENDING_BALANCE, added to queue
    """
    transfer = await get_transfer_by_request_id(db, request_id)

    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transfer not found: {request_id}",
        )

    # Only allow approve for PENDING_APPROVAL status
    if transfer.status != TransferStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve transfer in status '{transfer.status}'. Expected 'pending_approval'.",
        )

    log.info(
        "Approving transfer",
        extra={"request_id": request_id, "amount": str(transfer.amount)},
    )

    try:
        # Check if balance already reserved (wallet_id set during create)
        if transfer.wallet_id:
            # Balance already reserved - just need to get wallet/vault info
            wallet = await db.get(WalletModel, transfer.wallet_id)
            vault = await db.get(VaultModel, transfer.vault_id)
            asset = await db.get(AssetModel, transfer.asset_id)
            
            if not wallet or not vault:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Transfer has wallet_id but wallet/vault not found",
                )
            
            log.info(
                "Balance already reserved, skipping reservation",
                extra={"request_id": request_id, "wallet_id": str(transfer.wallet_id)},
            )
        else:
            # Try to reserve balance on HOT wallet
            wallet, vault, asset = await select_and_reserve_hot_wallet(
                db=db,
                contract_address=transfer.contract_address,
                blockchain=transfer.blockchain,
                amount=transfer.amount,
            )

            # Update transfer with wallet info
            await update_transfer_with_wallet(db, transfer, wallet, vault, asset)
            await db.commit()

        # Publish balance_ready event
        await publish_balance_ready(
            request_id=request_id,
            source_vault_id=vault.provider_vault_id,
            source_address=wallet.address,
            destination_address=transfer.destination_address,
            destination_tag=transfer.destination_tag,
            amount=str(transfer.amount),
            contract_address=transfer.contract_address,
            blockchain=transfer.blockchain,
        )

        log.info(
            "Transfer approved, balance reserved",
            extra={
                "request_id": request_id,
                "source_address": wallet.address,
                "vault": vault.name,
            },
        )

        return ApproveResponse(
            request_id=request_id,
            status=TransferStatus.PENDING.value,
            source_vault_id=vault.provider_vault_id,
            source_address=wallet.address,
            message="Balance reserved, ready for signing",
        )

    except (NoHotWalletError, InsufficientBalanceError) as e:
        # No balance available - add to pending_balance queue
        log.warning(
            f"Insufficient balance for approved transfer, adding to queue: {e}",
            extra={"request_id": request_id},
        )

        transfer.status = TransferStatus.PENDING_BALANCE.value
        await db.commit()

        return ApproveResponse(
            request_id=request_id,
            status=TransferStatus.PENDING_BALANCE.value,
            source_vault_id=None,
            source_address=None,
            message=f"Waiting for HOT wallet balance: {str(e)}",
        )

    except Exception as e:
        await db.rollback()
        log.error(f"Failed to approve transfer: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve transfer: {str(e)}",
        )


@router.post(
    "/{request_id}/reject",
    summary="Reject transfer",
)
async def reject_transfer(
    request_id: str,
    request: RejectRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reject transfer (no balance to release since Approval First).

    Called by Workflow when AML/Policy rejects the transaction.
    """
    transfer = await get_transfer_by_request_id(db, request_id)

    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transfer not found: {request_id}",
        )

    if transfer.is_final:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transfer already finalized: {transfer.status}",
        )

    # Release reserve if balance was reserved (PENDING status)
    if transfer.status == TransferStatus.PENDING.value and transfer.wallet_id:
        await release_reserve(db, transfer.wallet_id, transfer.amount)

    # Update status
    await update_transfer_status(
        db=db,
        request_id=request_id,
        status=TransferStatus.REJECTED,
        error_message=request.reason,
    )

    await db.commit()

    log.info(f"Transfer rejected: {request_id}", extra={"reason": request.reason})

    return {"status": "rejected", "request_id": request_id}


@router.post(
    "/{request_id}/signing",
    summary="Update transfer to signing status",
)
async def update_transfer_signing(
    request_id: str,
    request: SigningRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update transfer to signing status.

    Called by Workflow/Signer when transaction is submitted to Fireblocks.
    """
    transfer = await get_transfer_by_request_id(db, request_id)

    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transfer not found: {request_id}",
        )

    await update_transfer_status(
        db=db,
        request_id=request_id,
        status=TransferStatus.SIGNING,
        provider_tx_id=request.provider_tx_id,
    )

    await db.commit()

    return {
        "status": "signing",
        "request_id": request_id,
        "provider_tx_id": request.provider_tx_id,
    }


@router.post(
    "/{request_id}/complete",
    summary="Complete transfer (release + deduct balance)",
)
async def complete_transfer(
    request_id: str,
    request: CompleteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Complete transfer when transaction is confirmed.

    Called by webhook handler when Fireblocks confirms the transaction.
    Releases reserved balance and deducts from wallet.
    """
    transfer = await get_transfer_by_request_id(db, request_id)

    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transfer not found: {request_id}",
        )

    if transfer.is_final:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Transfer already finalized: {transfer.status}",
        )

    # Complete balance (release reserve + deduct)
    if transfer.wallet_id:
        completed = await complete_transfer_balance(
            db=db,
            wallet_id=transfer.wallet_id,
            amount=transfer.amount,
        )

        if not completed:
            log.error(f"Failed to complete transfer balance for {request_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update wallet balance",
            )

    # Update status
    await update_transfer_status(
        db=db,
        request_id=request_id,
        status=TransferStatus.COMPLETED,
        tx_hash=request.tx_hash,
    )

    await db.commit()

    log.info(f"Transfer completed: {request_id}", extra={"tx_hash": request.tx_hash})

    return {"status": "completed", "request_id": request_id, "tx_hash": request.tx_hash}


@router.post(
    "/{request_id}/cancel",
    summary="Cancel transfer",
)
async def cancel_transfer_endpoint(
    request_id: str,
    request: CancelRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Cancel a transfer (only pending_approval, pending_balance or pending).

    If balance was reserved, it will be released.
    """
    transfer = await get_transfer_by_request_id(db, request_id)

    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transfer not found: {request_id}",
        )

    if not transfer.is_cancellable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel transfer in status '{transfer.status}'",
        )

    try:
        await cancel_transfer(db, transfer, request.reason)
        await db.commit()

        log.info(f"Transfer cancelled: {request_id}", extra={"reason": request.reason})

        return {
            "status": "cancelled",
            "request_id": request_id,
            "message": f"Transfer cancelled: {request.reason}",
        }

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        await db.rollback()
        log.error(f"Failed to cancel transfer {request_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel transfer: {str(e)}",
        )


# ============================================================================
# Queue Statistics
# ============================================================================


@router.get(
    "/queue/stats",
    response_model=QueueStatsResponse,
    summary="Get pending balance queue statistics",
)
async def get_queue_stats(
    db: AsyncSession = Depends(get_db),
) -> QueueStatsResponse:
    """
    Get statistics for the pending_balance queue.

    Returns count, total amount, and age of oldest pending transfer.
    """
    stats = await get_pending_balance_queue_stats(db)
    return QueueStatsResponse(**stats)


@router.post(
    "/queue/process",
    summary="Process pending balance queue",
)
async def process_queue(
    db: AsyncSession = Depends(get_db),
    limit: int = 10,
) -> dict:
    """
    Process transfers waiting in pending_balance queue.
    
    Tries to reserve balance and send transfers that were waiting for funds.
    """
    from app.models.transfer import TransferModel
    from app.enums.status import TransferStatus
    from app.dao.transfer import process_pending_balance_transfer
    from app.broker.publisher import publish_transfer_created
    
    # Get pending_balance transfers ordered by created_at
    stmt = (
        select(TransferModel)
        .where(TransferModel.status == TransferStatus.PENDING_BALANCE.value)
        .order_by(TransferModel.created_at.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    transfers = result.scalars().all()
    
    processed = 0
    failed = 0
    
    for transfer in transfers:
        success = await process_pending_balance_transfer(db, transfer)
        if success:
            # Get source vault provider_id and fireblocks_asset_id for workflow
            source_vault_id = None
            fireblocks_asset_id = None
            if transfer.vault_id:
                from app.models import VaultModel
                vault = await db.get(VaultModel, transfer.vault_id)
                if vault:
                    source_vault_id = vault.provider_vault_id
            if transfer.asset_id:
                from app.models import AssetModel
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
                network=asset_model.network if asset_model else (transfer.network or ""),
                source_vault_id=source_vault_id,
                source_address=transfer.source_address,
                fireblocks_asset_id=fireblocks_asset_id,
            )
            processed += 1
            log.info(f"✅ Processed pending transfer: {transfer.request_id}")
        else:
            failed += 1
    
    await db.commit()
    
    return {
        "processed": processed,
        "failed": failed,
        "remaining": len(transfers) - processed,
    }
