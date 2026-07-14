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
    """
    Process webhook events from Fireblocks.

    Fireblocks sends notifications about:
    - **transaction.created** - transaction creation
    - **transaction.status_updated** - transaction status change
    - **transaction.approval_status_updated** - approval status change
    - **vault_account.added** - vault account added
    - **vault_account.asset.added** - asset added to vault

    For incoming deposits:
    1. Transaction record is created/updated in DB
    2. Wallet balance is updated when COMPLETED
    3. Only technical data is stored (no business logic)

    Headers:
    - **Fireblocks-Signature**: Signature for request validation
    """
    # Read raw request body
    raw_body = await request.body()

    # Validate signature (disabled only for local environment)
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

    # Parse payload
    try:
        payload = FireblocksWebhookPayloadSchema.model_validate_json(raw_body)
    except ValidationError as e:
        log.error(f"❌ Error parsing webhook payload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # Process event
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
        # Return 200 so Fireblocks doesn't retry
        # Errors are logged for analysis
        return {"status": "error", "message": str(e)}


# ==================== Webhook Management Endpoints ====================


@router.get("/fireblocks/manage", summary="Get list of all webhooks")
async def list_webhooks():
    """Get list of all registered webhooks in Fireblocks."""
    try:
        provider = get_provider()
        webhooks = await provider.get_webhooks()
        return {"webhooks": webhooks, "count": len(webhooks)}
    except Exception as e:
        log.error(f"❌ Error getting webhook list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fireblocks/manage/{webhook_id}", summary="Get webhook info")
async def get_webhook(webhook_id: str):
    """Get information about specific webhook by ID."""
    try:
        provider = get_provider()
        webhook = await provider.get_webhook(webhook_id)
        return webhook
    except Exception as e:
        log.error(f"❌ Error getting webhook {webhook_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
