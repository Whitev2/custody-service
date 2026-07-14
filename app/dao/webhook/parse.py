"""Webhook parsing utilities."""

from decimal import Decimal

from app.schemas.webhooks import TransactionDetailsSchema


def parse_amount(tx: TransactionDetailsSchema) -> Decimal:
    """Парсинг суммы транзакции"""
    if tx.amountInfo and tx.amountInfo.amount:
        return Decimal(tx.amountInfo.amount)
    if tx.amount is not None:
        return Decimal(str(tx.amount))
    return Decimal("0")


def parse_net_amount_decimal(tx: TransactionDetailsSchema) -> Decimal:
    """Парсинг netAmount как Decimal"""
    if tx.amountInfo and tx.amountInfo.netAmount:
        return Decimal(tx.amountInfo.netAmount)
    if tx.netAmount is not None:
        return Decimal(str(tx.netAmount))
    # fallback на amount
    return parse_amount(tx)


def parse_amount_usd(tx: TransactionDetailsSchema) -> Decimal | None:
    """Парсинг суммы в USD"""
    if tx.amountInfo and tx.amountInfo.amountUSD:
        return Decimal(tx.amountInfo.amountUSD)
    if tx.amountUSD is not None:
        return Decimal(str(tx.amountUSD))
    return None


def parse_net_amount(tx: TransactionDetailsSchema) -> Decimal | None:
    """Парсинг чистой суммы"""
    if tx.amountInfo and tx.amountInfo.netAmount:
        return Decimal(tx.amountInfo.netAmount)
    if tx.netAmount is not None:
        return Decimal(str(tx.netAmount))
    return None
