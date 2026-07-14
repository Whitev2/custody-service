"""
RabbitMQ Broker for Custody Service.

Custody is the source of truth for all transfer.* queues.
All queues are initialized on Custody startup.

Queues:
- transfer.created       (Custody → Workflow)
- transfer.aml.requested (Workflow → AML)
- transfer.aml.completed (AML → Workflow)
- transfer.review.required (Workflow → UI)
- transfer.approved      (Workflow → Custody)
- transfer.rejected      (Workflow → Custody)

Consumers:
- TransferApprovedConsumer: Receives approved transfers with JWT
- TransferRejectedConsumer: Receives rejected transfers

Publishers:
- publish_transfer_created: Notify Workflow about new transfer
- publish_balance_ready: Notify Workflow that balance is ready
"""

from .queues import (
    init_transfer_queues,
    close_transfer_queues,
    get_queue_manager,
    QUEUES,
    TRANSFER_EXCHANGE,
)
from .consumer import (
    TransferRejectedConsumer,
    start_rejected_consumer,
    stop_rejected_consumer,
    TransferApprovedConsumer,
    start_approved_consumer,
    stop_approved_consumer,
)
from .publisher import (
    publish_transfer_created,
    publish_balance_ready,
    close_publisher,
)

__all__ = [
    # Queue initialization
    "init_transfer_queues",
    "close_transfer_queues",
    "get_queue_manager",
    "QUEUES",
    "TRANSFER_EXCHANGE",
    # Consumers
    "TransferRejectedConsumer",
    "start_rejected_consumer",
    "stop_rejected_consumer",
    "TransferApprovedConsumer",
    "start_approved_consumer",
    "stop_approved_consumer",
    # Publishers
    "publish_transfer_created",
    "publish_balance_ready",
    "close_publisher",
]

