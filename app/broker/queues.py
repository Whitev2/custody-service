"""
RabbitMQ Queue Configuration and Initialization.

Custody is the source of truth for all transfer.* queues.
Other services (Workflow, AML) connect to existing queues.
"""

import logging
from typing import Optional

import aio_pika
from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

from app.config import cfg

log = logging.getLogger(__name__)


# ============== Queue Configuration ==============

# Message TTL: 7 days
DEFAULT_MESSAGE_TTL_MS = 7 * 24 * 60 * 60 * 1000

# Main exchange
TRANSFER_EXCHANGE = "transfer.exchange"

# Dead Letter Exchange
DLX_EXCHANGE = "transfer.dlx"

# Queue definitions: {name: {routing_key, dlq: bool}}
QUEUES = {
    # Custody → Workflow: New transfer created
    "transfer.created": {
        "routing_key": "transfer.created",
        "dlq": True,
    },
    
    # Workflow → AML: Request risk analysis
    "transfer.aml.requested": {
        "routing_key": "transfer.aml.requested",
        "dlq": True,
    },
    
    # AML → Workflow: Risk analysis completed
    "transfer.aml.completed": {
        "routing_key": "transfer.aml.completed",
        "dlq": True,
    },
    
    # Workflow → UI: Manual review required
    "transfer.review.required": {
        "routing_key": "transfer.review.required",
        "dlq": False,
    },
    
    # Workflow → Custody: Approved transfer with signed JWT
    "transfer.approved": {
        "routing_key": "transfer.approved",
        "dlq": True,
    },
    
    # Workflow → Custody: Rejected transfer
    "transfer.rejected": {
        "routing_key": "transfer.rejected",
        "dlq": True,
    },
}


class TransferQueueManager:
    """
    Manages RabbitMQ queues for the transfer pipeline.
    
    Custody initializes all queues on startup.
    Other services connect to existing queues.
    """
    
    def __init__(self):
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[AbstractChannel] = None
        self.exchange: Optional[AbstractExchange] = None
        self.dlx_exchange: Optional[AbstractExchange] = None
        self._queues: dict[str, AbstractQueue] = {}
    
    async def initialize(self) -> None:
        """Initialize connection and declare all queues."""
        log.info("Initializing transfer queues...")
        
        self.connection = await aio_pika.connect_robust(
            cfg.rabbitmq.connection_string
        )
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=10)
        
        # Declare main exchange
        self.exchange = await self.channel.declare_exchange(
            TRANSFER_EXCHANGE,
            ExchangeType.TOPIC,
            durable=True,
        )
        log.info(f"Declared exchange: {TRANSFER_EXCHANGE}")
        
        # Declare DLX exchange
        self.dlx_exchange = await self.channel.declare_exchange(
            DLX_EXCHANGE,
            ExchangeType.DIRECT,
            durable=True,
        )
        log.info(f"Declared DLX exchange: {DLX_EXCHANGE}")
        
        # Declare all queues
        for queue_name, config in QUEUES.items():
            await self._declare_queue(queue_name, config)
        
        log.info(f"✅ All {len(QUEUES)} transfer queues initialized")
    
    async def _declare_queue(self, queue_name: str, config: dict) -> None:
        """Declare a queue with optional DLQ."""
        routing_key = config.get("routing_key", queue_name)
        has_dlq = config.get("dlq", False)
        
        arguments = {
            "x-message-ttl": DEFAULT_MESSAGE_TTL_MS,
        }
        
        # Create DLQ if enabled
        if has_dlq:
            dlq_name = f"{queue_name}.dlq"
            
            dlq = await self.channel.declare_queue(
                dlq_name,
                durable=True,
            )
            await dlq.bind(self.dlx_exchange, routing_key=dlq_name)
            
            arguments["x-dead-letter-exchange"] = DLX_EXCHANGE
            arguments["x-dead-letter-routing-key"] = dlq_name
        
        # Declare main queue
        queue = await self.channel.declare_queue(
            queue_name,
            durable=True,
            arguments=arguments,
        )
        await queue.bind(self.exchange, routing_key=routing_key)
        
        self._queues[queue_name] = queue
        log.debug(f"Declared queue: {queue_name} → {routing_key}")
    
    async def close(self) -> None:
        """Close the connection."""
        if self.connection:
            await self.connection.close()
            log.info("Closed transfer queue connection")
    
    def get_queue(self, name: str) -> Optional[AbstractQueue]:
        """Get a declared queue by name."""
        return self._queues.get(name)


# Singleton
_queue_manager: Optional[TransferQueueManager] = None


async def init_transfer_queues() -> TransferQueueManager:
    """Initialize all transfer queues (called on Custody startup)."""
    global _queue_manager
    _queue_manager = TransferQueueManager()
    await _queue_manager.initialize()
    return _queue_manager


async def close_transfer_queues() -> None:
    """Close the queue manager."""
    global _queue_manager
    if _queue_manager:
        await _queue_manager.close()
        _queue_manager = None


def get_queue_manager() -> Optional[TransferQueueManager]:
    """Get the queue manager instance."""
    return _queue_manager

