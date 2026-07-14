"""Webhook API endpoints."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import cfg, log
from app.storage import get_db
from app.dao.webhook import process_webhook
from app.schemas.webhooks import FireblocksWebhookPayloadSchema
from app.services.custody import get_provider
from app.services.webhook_signature import get_webhook_validator

router = APIRouter(prefix="/webhook", tags=["Webhook"])


@router.post("/fireblocks", summary="Process Fireblocks webhook")
async def fireblocks_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    fireblocks_signature: str | None = Header(None, alias="Fireblocks-Signature"),
):
    """Приём webhook-событий от Fireblocks (tx + vault account)."""
    raw_body = await request.body()

    # подпись проверяем везде кроме local/dev
    if cfg.app.STAND in ["local", "dev"]:
        log.warning("⚠️ Webhook signature validation disabled")
    else:
        if not fireblocks_signature:
            log.warning("❌ Webhook without signature")
            raise HTTPException(
                status_code=401, detail="Missing Fireblocks-Signature header"
            )

        validator = get_webhook_validator()
        if not validator.verify_signature(raw_body, fireblocks_signature):
            log.warning("❌ Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = FireblocksWebhookPayloadSchema.model_validate_json(raw_body)
    except ValidationError as e:
        log.error(f"❌ Error parsing webhook payload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    try:
        result = await process_webhook(
            db=db,
            payload=payload,
            raw_body=raw_body.decode("utf-8"),
        )

        log.info(f"✅ Webhook processed: {result}")
        return result

    except Exception as e:
        log.error(f"❌ Error processing webhook: {e}", exc_info=True)
        await db.rollback()
        # 200 чтобы Fireblocks не ретраил
        return {"status": "error", "message": str(e)}


@router.get("/fireblocks/manage", summary="Get list of all webhooks")
async def list_webhooks():
    try:
        provider = get_provider()
        webhooks = await provider.get_webhooks()
        return {"webhooks": webhooks, "count": len(webhooks)}
    except Exception as e:
        log.error(f"❌ Error getting webhook list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fireblocks/manage/{webhook_id}", summary="Get webhook info")
async def get_webhook(webhook_id: str):
    try:
        provider = get_provider()
        webhook = await provider.get_webhook(webhook_id)
        return webhook
    except Exception as e:
        log.error(f"❌ Error getting webhook {webhook_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
