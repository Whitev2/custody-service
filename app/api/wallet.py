"""Wallet API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.database import get_db
from app.config import log
from app.dao.wallet import (
    get_or_create_wallet_for_vault,
    get_or_create_wallet_for_existing_vault,
)
from app.schemas.wallet import WalletWithVaultResponse

router = APIRouter(prefix="/wallet", tags=["Wallet"])


@router.get("/get", summary="Получить кошелек для vault")
async def get_wallet_endpoint(
    vault_name: str = Query(
        ..., description="Имя vault (USER_{user_id}, POOL_1, и т.д.)"
    ),
    currency: str = Query(..., description="Символ валюты (USDT, ETH, BTC)"),
    contract_address: str | None = Query(
        None, description="Адрес контракта (None для нативных)"
    ),
    db: AsyncSession = Depends(get_db),
) -> WalletWithVaultResponse:
    """
    Получить кошелек для указанного vault и валюты.

    Если vault не существует - создает его.
    Если asset не активирован - активирует его.

    Args:
        vault_name: Имя vault
        currency: Символ валюты
        contract_address: Адрес контракта (None для нативных)

    Returns:
        WalletWithVaultResponse с адресом кошелька и vault_id
    """
    try:
        wallet_info = await get_or_create_wallet_for_vault(
            db=db,
            vault_name=vault_name,
            currency=currency,
            contract_address=contract_address,
        )

        return wallet_info

    except ValueError as e:
        log.warning(f"Bad request in get_wallet: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        msg = str(e)
        log.error(f"Error getting wallet: {e}")
        if "fireblocks" in msg.lower() or "api error" in msg.lower():
            raise HTTPException(status_code=502, detail=msg)
        raise HTTPException(status_code=500, detail=msg)


@router.get("/get-for-vault", summary="Получить кошелек для существующего vault")
async def get_wallet_for_existing_vault_endpoint(
    custody_vault_id: str = Query(..., description="ID vault из custody сервиса"),
    currency: str = Query(..., description="Символ валюты (USDT, ETH, BTC)"),
    contract_address: str | None = Query(
        None, description="Адрес контракта (None для нативных)"
    ),
    db: AsyncSession = Depends(get_db),
) -> WalletWithVaultResponse:
    """
    Получить кошелек для существующего vault по custody_vault_id.

    Если vault не существует - создает его.
    Если asset не активирован - активирует его.

    Args:
        custody_vault_id: ID vault из custody сервиса
        currency: Символ валюты
        contract_address: Адрес контракта (None для нативных)

    Returns:
        WalletWithVaultResponse с адресом кошелька и vault_id
    """
    try:
        wallet_info = await get_or_create_wallet_for_existing_vault(
            db=db,
            custody_vault_id=custody_vault_id,
            currency=currency,
            contract_address=contract_address,
        )

        return wallet_info

    except ValueError as e:
        log.warning(f"Bad request in get_wallet_for_existing_vault: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        msg = str(e)
        log.error(f"Error getting wallet for existing vault: {e}")
        if "fireblocks" in msg.lower() or "api error" in msg.lower():
            raise HTTPException(status_code=502, detail=msg)
        raise HTTPException(status_code=500, detail=msg)
