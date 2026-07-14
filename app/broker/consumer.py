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


_rejected_consumer: Optional["TransferRejectedConsumer"] = None


class TransferRejectedConsumer:
    # На reject размораживаем зарезервированный баланс HOT-кошелька.

    def __init__(
        self,
        callback: Callable[[dict], Awaitable[None]],
    ):
        self.callback = callback
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.Channel] = None
        self._consumer_tag: Optional[str] = None

    async def start(self) -> None:
        log.info("Starting Transfer Rejected Consumer...")

        queue_name = "transfer.rejected"

        try:
            self.connection = await aio_pika.connect_robust(
                cfg.rabbitmq.connection_string
            )
            self.channel = await self.connection.channel()
            await self.channel.set_qos(prefetch_count=10)

            # очередь уже создана init_transfer_queues
            queue = await self.channel.declare_queue(queue_name, passive=True)

            self._consumer_tag = await queue.consume(self._on_message)

            log.info(f"Transfer Rejected Consumer started - listening on {queue_name}")

        except Exception as e:
            log.error(f"Failed to start Transfer Rejected Consumer: {e}")
            raise

    async def stop(self) -> None:
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
    request_id = body["request_id"]
    reason = body.get("reason", "Rejected by workflow")

    async with db_manager.get_db_local() as db:
        transfer = await get_transfer_by_request_id(db, request_id)

        if not transfer:
            log.warning(f"Transfer not found for rejection: {request_id}")
            return

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

        if not transfer.is_internal:
            await notify_backend_payout_status(request_id, "rejected")


async def start_rejected_consumer() -> None:
    global _rejected_consumer
    _rejected_consumer = TransferRejectedConsumer(callback=handle_transfer_rejected)
    await _rejected_consumer.start()


async def stop_rejected_consumer() -> None:
    global _rejected_consumer
    if _rejected_consumer:
        await _rejected_consumer.stop()
        _rejected_consumer = None


_approved_consumer: Optional["TransferApprovedConsumer"] = None


class TransferApprovedConsumer:
    # Workflow подписывает своим SIGNER-ключом и шлёт JWT + transaction_body,
    # custody исполняет транзакцию в Fireblocks по этому JWT.

    def __init__(
        self,
        callback: Callable[[dict], Awaitable[None]],
    ):
        self.callback = callback
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.Channel] = None
        self._consumer_tag: Optional[str] = None

    async def start(self) -> None:
        log.info("Starting Transfer Approved Consumer...")

        queue_name = "transfer.approved"

        try:
            self.connection = await aio_pika.connect_robust(
                cfg.rabbitmq.connection_string
            )
            self.channel = await self.connection.channel()
            await self.channel.set_qos(prefetch_count=10)

            # очередь уже создана init_transfer_queues
            queue = await self.channel.declare_queue(queue_name, passive=True)

            self._consumer_tag = await queue.consume(self._on_message)

            log.info(f"Transfer Approved Consumer started - listening on {queue_name}")

        except Exception as e:
            log.error(f"Failed to start Transfer Approved Consumer: {e}")
            raise

    async def stop(self) -> None:
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
    request_id = body["request_id"]
    jwt_token = body.get("jwt_token")
    api_key = body.get("api_key")
    transaction_body = body.get("transaction_body")

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

        try:
            service = fireblocks_service()

            tx_result = await service.create_transaction_with_jwt(
                jwt_token=jwt_token,
                api_key=api_key,
                transaction_body=transaction_body,
            )

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

            if not transfer.is_internal:
                await notify_backend_payout_status(request_id, "failed")


async def start_approved_consumer() -> None:
    global _approved_consumer
    _approved_consumer = TransferApprovedConsumer(callback=handle_transfer_approved)
    await _approved_consumer.start()


async def stop_approved_consumer() -> None:
    global _approved_consumer
    if _approved_consumer:
        await _approved_consumer.stop()
        _approved_consumer = None
