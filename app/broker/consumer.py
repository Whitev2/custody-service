"""
RabbitMQ Consumers for Custody Service.

Consumes transfer.approved and transfer.rejected events from Workflow.
"""

import json
from typing import Callable, Awaitable, Optional

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.config import cfg, log
from app.services.payout_callback import notify_backend_payout_status
from app.storage import db_manager
from app.dao.transfer import get_transfer_by_request_id, release_reserve
from app.enums.status import TransferStatus
from app.services.custody.fireblocks.service import fireblocks_service


# ============================================================================
# Transfer Rejected Consumer
# ============================================================================

_rejected_consumer: Optional["TransferRejectedConsumer"] = None


class TransferRejectedConsumer:
    """
    Consumer for transfer.rejected events from Workflow.

    When Workflow rejects a transfer, this consumer unfreezes
    the reserved balance on the HOT wallet.
    """

    def __init__(
        self,
        callback: Callable[[dict], Awaitable[None]],
    ):
        self.callback = callback
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.Channel] = None
        self._consumer_tag: Optional[str] = None

    async def start(self) -> None:
        """Start consuming transfer.rejected messages."""
        log.info("Starting Transfer Rejected Consumer...")

        queue_name = "transfer.rejected"

        try:
            self.connection = await aio_pika.connect_robust(
                cfg.rabbitmq.connection_string
            )
            self.channel = await self.connection.channel()
            await self.channel.set_qos(prefetch_count=10)

            # Get queue (already created by init_transfer_queues)
            queue = await self.channel.declare_queue(queue_name, passive=True)

            self._consumer_tag = await queue.consume(self._on_message)

            log.info(f"Transfer Rejected Consumer started - listening on {queue_name}")

        except Exception as e:
            log.error(f"Failed to start Transfer Rejected Consumer: {e}")
            raise

    async def stop(self) -> None:
        """Stop the consumer."""
        if self.channel and self._consumer_tag:
            try:
                await self.channel.cancel(self._consumer_tag)
            except Exception as e:
                log.warning(f"Error cancelling consumer: {e}")

        if self.connection:
            try:
                await self.connection.close()
            except Exception as e:
                log.warning(f"Error closing connection: {e}")

        log.info("Transfer Rejected Consumer stopped")

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """Handle incoming transfer.rejected message."""
        async with message.process(requeue=False):
            request_id = None
            try:
                body = json.loads(message.body.decode())
                request_id = body.get("request_id", "unknown")
                reason = body.get("reason", "No reason provided")

                log.info(
                    "Processing transfer.rejected",
                    extra={
                        "request_id": request_id,
                        "reason": reason,
                    },
                )

                await self.callback(body)

                log.info(
                    "Successfully processed transfer.rejected",
                    extra={"request_id": request_id},
                )

            except Exception as e:
                log.error(
                    f"Failed to process transfer.rejected: {e}",
                    extra={"request_id": request_id},
                    exc_info=True,
                )
                raise


async def handle_transfer_rejected(body: dict) -> None:
    """
    Handle a transfer.rejected message from Workflow.

    1. Find the transfer by request_id
    2. Release the reserved balance
    3. Update transfer status to REJECTED
    4. Notify backend about failure
    """
    request_id = body["request_id"]
    reason = body.get("reason", "Rejected by workflow")

    async with db_manager.get_db_local() as db:
        transfer = await get_transfer_by_request_id(db, request_id)

        if not transfer:
            log.warning(f"Transfer not found for rejection: {request_id}")
            return

        # Release reserved balance if wallet was assigned
        if transfer.wallet_id:
            released = await release_reserve(
                db=db,
                wallet_id=transfer.wallet_id,
                amount=transfer.amount,
            )
            if released:
                log.info(
                    "Released reserved balance for rejected transfer",
                    extra={
                        "request_id": request_id,
                        "amount": str(transfer.amount),
                    },
                )

        # Update transfer status
        transfer.status = TransferStatus.REJECTED.value
        transfer.error_message = reason

        await db.commit()

        log.info(
            "Transfer rejected and balance unfrozen",
            extra={
                "request_id": request_id,
                "reason": reason,
            },
        )

        # Notify backend about rejection
        if not transfer.is_internal:
            await notify_backend_payout_status(request_id, "rejected")


async def start_rejected_consumer() -> None:
    """Start the rejected consumer instance."""
    global _rejected_consumer
    _rejected_consumer = TransferRejectedConsumer(callback=handle_transfer_rejected)
    await _rejected_consumer.start()


async def stop_rejected_consumer() -> None:
    """Stop the rejected consumer instance."""
    global _rejected_consumer
    if _rejected_consumer:
        await _rejected_consumer.stop()
        _rejected_consumer = None


# ============================================================================
# Transfer Approved Consumer
# ============================================================================

_approved_consumer: Optional["TransferApprovedConsumer"] = None


class TransferApprovedConsumer:
    """
    Consumer for transfer.approved events from Workflow.

    Workflow signs transactions with its Fireblocks SIGNER key and
    sends JWT + transaction_body. Custody uses the JWT to execute
    the transaction in Fireblocks.
    """

    def __init__(
        self,
        callback: Callable[[dict], Awaitable[None]],
    ):
        self.callback = callback
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.Channel] = None
        self._consumer_tag: Optional[str] = None

    async def start(self) -> None:
        """Start consuming transfer.approved messages."""
        log.info("Starting Transfer Approved Consumer...")

        queue_name = "transfer.approved"

        try:
            self.connection = await aio_pika.connect_robust(
                cfg.rabbitmq.connection_string
            )
            self.channel = await self.connection.channel()
            await self.channel.set_qos(prefetch_count=10)

            # Get queue (already created by init_transfer_queues)
            queue = await self.channel.declare_queue(queue_name, passive=True)

            self._consumer_tag = await queue.consume(self._on_message)

            log.info(f"Transfer Approved Consumer started - listening on {queue_name}")

        except Exception as e:
            log.error(f"Failed to start Transfer Approved Consumer: {e}")
            raise

    async def stop(self) -> None:
        """Stop the consumer."""
        if self.channel and self._consumer_tag:
            try:
                await self.channel.cancel(self._consumer_tag)
            except Exception as e:
                log.warning(f"Error cancelling consumer: {e}")

        if self.connection:
            try:
                await self.connection.close()
            except Exception as e:
                log.warning(f"Error closing connection: {e}")

        log.info("Transfer Approved Consumer stopped")

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """Handle incoming transfer.approved message."""
        async with message.process(requeue=False):
            request_id = None
            try:
                body = json.loads(message.body.decode())
                request_id = body.get("request_id", "unknown")

                log.info(
                    "Processing transfer.approved", extra={"request_id": request_id}
                )

                await self.callback(body)

                log.info(
                    "Successfully processed transfer.approved",
                    extra={"request_id": request_id},
                )

            except Exception as e:
                log.error(
                    f"Failed to process transfer.approved: {e}",
                    extra={"request_id": request_id},
                    exc_info=True,
                )
                raise


async def handle_transfer_approved(body: dict) -> None:
    """
    Handle a transfer.approved message from Workflow.

    Message contains:
    - request_id: Transfer request ID
    - jwt_token: Pre-signed JWT from Workflow (SIGNER key)
    - api_key: Workflow's Fireblocks API key
    - transaction_body: Transaction payload (matches JWT bodyHash)

    Custody uses the JWT to execute the transaction in Fireblocks.
    """

    request_id = body["request_id"]
    jwt_token = body.get("jwt_token")
    api_key = body.get("api_key")
    transaction_body = body.get("transaction_body")

    # Validate required fields
    if not jwt_token or not api_key or not transaction_body:
        log.error(
            "Invalid transfer.approved message - missing jwt_token, api_key or transaction_body",
            extra={"request_id": request_id},
        )
        return

    async with db_manager.get_db_local() as db:
        transfer = await get_transfer_by_request_id(db, request_id)

        if not transfer:
            log.warning(f"Transfer not found for approval: {request_id}")
            return

        if transfer.status not in [
            TransferStatus.PENDING.value,
            TransferStatus.PENDING_BALANCE.value,
            TransferStatus.PENDING_APPROVAL.value,
        ]:
            log.warning(
                f"Transfer not in valid status for approval: {request_id}, status={transfer.status}"
            )
            return

        # Update status to SIGNING
        transfer.status = TransferStatus.SIGNING.value
        await db.commit()

        log.info(
            "Transfer approved, executing with Workflow JWT",
            extra={
                "request_id": request_id,
                "asset_id": transaction_body.get("assetId"),
                "amount": transaction_body.get("amount"),
                "destination": transaction_body.get("destination", {})
                .get("oneTimeAddress", {})
                .get("address"),
            },
        )

        # Send to Fireblocks using JWT from Workflow
        try:
            service = fireblocks_service()

            tx_result = await service.create_transaction_with_jwt(
                jwt_token=jwt_token,
                api_key=api_key,
                transaction_body=transaction_body,
            )

            # Update transfer with Fireblocks TX ID
            transfer.provider_tx_id = tx_result.get("id")
            transfer.status = TransferStatus.BROADCASTING.value
            await db.commit()

            log.info(
                "✅ Transaction created in Fireblocks via Workflow JWT",
                extra={
                    "request_id": request_id,
                    "fireblocks_tx_id": tx_result.get("id"),
                    "status": tx_result.get("status"),
                },
            )

        except Exception as e:
            log.error(f"Failed to create Fireblocks transaction: {e}", exc_info=True)
            transfer.status = TransferStatus.FAILED.value
            transfer.error_message = str(e)
            await db.commit()

            # Notify backend about failure
            if not transfer.is_internal:
                await notify_backend_payout_status(request_id, "failed")


async def start_approved_consumer() -> None:
    """Start the approved consumer instance."""
    global _approved_consumer
    _approved_consumer = TransferApprovedConsumer(callback=handle_transfer_approved)
    await _approved_consumer.start()


async def stop_approved_consumer() -> None:
    """Stop the approved consumer instance."""
    global _approved_consumer
    if _approved_consumer:
        await _approved_consumer.stop()
        _approved_consumer = None
