"""
Centralized payout callback service.

All payout status notifications to backend go through this single module.
"""

from typing import Literal

from app.services.http_client import http_client
from app.config import cfg, log


PayoutStatus = Literal["completed", "failed", "rejected", "cancelled"]


async def notify_backend_payout_status(
    request_id: str, status: PayoutStatus, tx_hash: str | None = None
) -> None:
    """
    Notify backend about payout status change.

    This is the SINGLE place for all payout callbacks.

    Args:
        request_id: Transfer request_id (UUID string)
        status: One of: completed, failed, rejected, cancelled
        tx_hash: Transaction hash (optional)
    """
    backend_url = cfg.app.BACKEND_URL
    if not backend_url:
        log.warning(
            "BACKEND_URL not configured, skipping callback",
            extra={"request_id": request_id, "status": status},
        )
        return

    url = f"{backend_url}/internal/payouts"
    payload = {
        "request_id": request_id,
        "status": status,
        "tx_hash": tx_hash,
    }

    try:
        session = http_client.get_session()
        async with session.post(url, json=payload) as response:
            if response.status == 200:
                log.info(
                    "✅ Payout callback sent",
                    extra={"request_id": request_id, "status": status},
                )
            else:
                error_text = await response.text()
                log.error(
                    f"❌ Payout callback failed: {response.status}",
                    extra={
                        "request_id": request_id,
                        "status": status,
                        "response": error_text[:500],
                    },
                )
    except Exception as e:
        log.error(
            f"❌ Payout callback error: {e}",
            extra={"request_id": request_id, "status": status},
        )
