from datetime import datetime, timezone

from app.enums import InvoiceStatusEnum, TransactionStatusEnum


def timestamp_to_datetime(ts: int | None) -> datetime | None:
    """Конвертация Unix timestamp в datetime"""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


FIREBLOCKS_TO_INVOICE_STATUS: dict[str, InvoiceStatusEnum] = {
    TransactionStatusEnum.SUBMITTED.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.QUEUED.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.PENDING_SIGNATURE.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.PENDING_AUTHORIZATION.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.PENDING_3RD_PARTY_MANUAL_APPROVAL.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.PENDING_3RD_PARTY.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.PENDING.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.BROADCASTING.value: InvoiceStatusEnum.PENDING,
    TransactionStatusEnum.CONFIRMING.value: InvoiceStatusEnum.CONFIRMING,
    TransactionStatusEnum.CONFIRMED.value: InvoiceStatusEnum.PAID,
    TransactionStatusEnum.COMPLETED.value: InvoiceStatusEnum.PAID,
    TransactionStatusEnum.PARTIALLY_COMPLETED.value: InvoiceStatusEnum.PAID,
    TransactionStatusEnum.CANCELLING.value: InvoiceStatusEnum.CANCELED,
    TransactionStatusEnum.CANCELLED.value: InvoiceStatusEnum.CANCELED,
    TransactionStatusEnum.REJECTED.value: InvoiceStatusEnum.CANCELED,
    TransactionStatusEnum.FAILED.value: InvoiceStatusEnum.FAILED,
    TransactionStatusEnum.TIMEOUT.value: InvoiceStatusEnum.TIMEOUT,
    TransactionStatusEnum.BLOCKED.value: InvoiceStatusEnum.FAILED,
}


def map_fireblocks_to_invoice_status(tx_status: str) -> InvoiceStatusEnum:
    """Преобразовать статус Fireblocks в статус заявки (InvoiceStatusEnum)."""
    return FIREBLOCKS_TO_INVOICE_STATUS.get(tx_status, InvoiceStatusEnum.PENDING)
