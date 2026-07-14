"""
Transaction Publishers for Custody Service.

Publishes transfer.created, balance_ready events and backend webhooks.
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

import aio_pika
from aio_pika import Message, DeliveryMode

from app.config import cfg

log = logging.getLogger(__name__)

# Routing keys for transfer flow
TRANSFER_CREATED_ROUTING_KEY = "transfer.created"
BALANCE_READY_ROUTING_KEY = "transfer.balance_ready"

# Backend webhook queue (direct to queue, no exchange)
CUSTODY_WEBHOOK_QUEUE = "custody.webhook"

# Global connection for publishing
_connection: Optional[aio_pika.RobustConnection] = None
_channel: Optional[aio_pika.Channel] = None
_exchange: Optional[aio_pika.Exchange] = None


async def _ensure_connection() -> aio_pika.Exchange:
    """Ensure we have a connection and return the exchange."""
    global _connection, _channel, _exchange

    if _exchange is not None:
        return _exchange

    _connection = await aio_pika.connect_robust(cfg.rabbitmq.connection_string)
    _channel = await _connection.channel()

    _exchange = await _channel.declare_exchange(
        cfg.rabbitmq.TRANSFER_EXCHANGE,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )

    log.info("Custody Publisher connection established")
    return _exchange


async def close_publisher() -> None:
    """Close the publisher connection."""
    global _connection, _channel, _exchange

    if _connection:
        await _connection.close()
        _connection = None
        _channel = None
        _exchange = None
        log.info("Custody Publisher connection closed")


async def publish_transfer_created(
    request_id: str,
    destination_address: str,
    destination_tag: Optional[str],
    amount: str,
    amount_usd: float,
    asset: str,
    contract_address: Optional[str],
    blockchain: str,
    currency: str,
    network: str,
    source_vault_id: Optional[str] = None,
    source_address: Optional[str] = None,
    fireblocks_asset_id: Optional[str] = None,
) -> bool:
    """
    Publish transfer.created event for external transfers.

    Called when Custody creates a new external transfer request.
    The message will be consumed by Workflow for AML/Policy processing.

    Note: Balance is reserved BEFORE publishing. source_vault_id and
    source_address are included so Signer knows where to send from.

    Args:
        request_id: Unique request ID for tracing
        destination_address: Destination wallet address
        destination_tag: Optional memo/tag
        amount: Transfer amount as decimal string
        amount_usd: USD equivalent
        asset: Asset symbol (ETH, USDT)
        contract_address: Token contract address (null for native)
        blockchain: Blockchain network
        source_vault_id: Reserved HOT vault ID (Fireblocks provider ID)
        source_address: Reserved HOT wallet address
        fireblocks_asset_id: Fireblocks asset ID (e.g., USDT_TRC20)

    Returns:
        True if published successfully, False otherwise
    """
    try:
        exchange = await _ensure_connection()

        message_body = {
            "request_id": request_id,
            "destination_address": destination_address,
            "destination_tag": destination_tag,
            "amount": amount,
            "amount_usd": amount_usd,
            "asset": asset,
            "contract_address": contract_address,
            "blockchain": blockchain,
            "currency": currency,
            "network": network,
            "source_vault_id": source_vault_id,
            "source_address": source_address,
            "fireblocks_asset_id": fireblocks_asset_id,
            "created_at": datetime.utcnow().isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        }

        message = Message(
            body=json.dumps(message_body).encode(),
            content_type="application/json",
            message_id=request_id,
            delivery_mode=DeliveryMode.PERSISTENT,
            headers={"request_id": request_id},
        )

        await exchange.publish(
            message,
            routing_key=TRANSFER_CREATED_ROUTING_KEY,
        )

        log.info(
            "Published transfer.created event",
            extra={
                "request_id": request_id,
                "amount": amount,
                "blockchain": blockchain,
                "destination": destination_address[:20] + "...",
            },
        )
        return True

    except Exception as e:
        log.error(
            f"Failed to publish transfer.created event: {e}",
            extra={"request_id": request_id},
            exc_info=True,
        )
        return False


async def publish_balance_ready(
    request_id: str,
    source_vault_id: str,
    source_address: str,
    destination_address: str,
    destination_tag: Optional[str],
    amount: str,
    contract_address: Optional[str],
    blockchain: str,
) -> bool:
    """
    Publish balance_ready event when HOT wallet balance is reserved.

    Called after approve when Custody successfully reserves balance.
    The message will be consumed by Workflow to proceed with signing.

    Args:
        request_id: Unique request ID for tracing
        source_vault_id: Selected HOT vault ID (Fireblocks provider ID)
        source_address: HOT wallet address
        destination_address: Destination wallet address
        destination_tag: Optional memo/tag
        amount: Transfer amount as decimal string
        contract_address: Token contract address (null for native)
        blockchain: Blockchain network

    Returns:
        True if published successfully, False otherwise
    """
    try:
        exchange = await _ensure_connection()

        message_body = {
            "request_id": request_id,
            "source_vault_id": source_vault_id,
            "source_address": source_address,
            "destination_address": destination_address,
            "destination_tag": destination_tag,
            "amount": amount,
            "contract_address": contract_address,
            "blockchain": blockchain,
            "reserved_at": datetime.utcnow().isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        }

        message = Message(
            body=json.dumps(message_body).encode(),
            content_type="application/json",
            message_id=request_id,
            delivery_mode=DeliveryMode.PERSISTENT,
            headers={"request_id": request_id},
        )

        await exchange.publish(
            message,
            routing_key=BALANCE_READY_ROUTING_KEY,
        )

        log.info(
            "Published balance_ready event",
            extra={
                "request_id": request_id,
                "source_address": source_address,
                "amount": amount,
            },
        )
        return True

    except Exception as e:
        log.error(
            f"Failed to publish balance_ready event: {e}",
            extra={"request_id": request_id},
            exc_info=True,
        )
        return False


async def publish_custody_webhook(
    custody_vault_id: UUID,
    amount: Decimal,
    blockchain: str,
    currency: str,
    network: str,
    status: str,
    tx_hash: Optional[str] = None,
    confirmations: Optional[int] = None,
    asset_id: Optional[str] = None,
) -> bool:
    """
    Publish custody deposit webhook to backend via RabbitMQ.

    Sends message to custody.webhook queue for guaranteed delivery.
    Backend consumer will process it and update invoice/transaction.

    Args:
        custody_vault_id: Vault UUID from custody service
        amount: Deposit amount
        blockchain: Blockchain name (BSC, ETHEREUM, TRON)
        currency: Currency symbol (USDT, BTC)
        network: Network type (ERC20, TRC20, BASE_ASSET)
        status: Transaction status
        tx_hash: Blockchain transaction hash
        confirmations: Number of confirmations
        asset_id: Fireblocks asset ID

    Returns:
        True if published successfully, False otherwise
    """
    global _connection, _channel

    try:
        # Ensure connection
        if _connection is None or _connection.is_closed:
            _connection = await aio_pika.connect_robust(cfg.rabbitmq.connection_string)
            log.info("Custody Publisher connection established for webhooks")
        
        if _channel is None or _channel.is_closed:
            _channel = await _connection.channel()

        # Declare queue to ensure it exists
        queue = await _channel.declare_queue(
            CUSTODY_WEBHOOK_QUEUE,
            durable=True,
        )

        message_body = {
            "custody_vault_id": str(custody_vault_id),
            "amount": str(amount),
            "blockchain": blockchain,
            "currency": currency,
            "network": network,
            "status": status,
            "tx_hash": tx_hash,
            "confirmations": confirmations,
            "asset_id": asset_id,
        }

        message = Message(
            body=json.dumps(message_body).encode(),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            headers={
                "custody_vault_id": str(custody_vault_id),
                "tx_hash": tx_hash or "",
            },
        )

        # Publish directly to queue via default exchange
        await _channel.default_exchange.publish(
            message,
            routing_key=CUSTODY_WEBHOOK_QUEUE,
        )

        log.info(
            f"📤 Published custody webhook to backend: "
            f"vault_id={custody_vault_id}, amount={amount}, "
            f"status={status}, tx_hash={tx_hash}"
        )
        return True

    except Exception as e:
        log.error(
            f"❌ Failed to publish custody webhook: {e}",
            extra={
                "custody_vault_id": str(custody_vault_id),
                "tx_hash": tx_hash,
            },
            exc_info=True,
        )
        return False
