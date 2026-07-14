"""
Маппинг currency + contract_address на Fireblocks asset ID.
"""

from app.config import log
from app.services.custody.fireblocks.utils import mapping_native_tokens


async def map_currency_to_fireblocks_asset(
    currency: str,
    contract_address: str,
    is_testnet: bool,
    fb_assets: list[dict],
) -> str | None:
    """
    Определить Fireblocks asset ID по currency и contract_address.

    Args:
        currency: Символ валюты (USDT, ETH, BNB, etc)
        contract_address: Адрес контракта (пустая строка для нативных токенов)
        is_testnet: True для testnet, False для mainnet
        fb_assets: Список всех доступных Fireblocks assets

    Returns:
        Fireblocks asset ID или None если не найден
    """
    currency_upper = currency.upper()

    # 1. Если contract_address пустой - это нативный токен, маппим через словарь
    if not contract_address:
        native_mapping = mapping_native_tokens()
        env = "dev" if is_testnet else "prod"

        if env in native_mapping and currency_upper in native_mapping[env]:
            asset_id = native_mapping[env][currency_upper]["asset_id"]
            log.debug(f"Mapped native token {currency_upper} -> {asset_id}")
            return asset_id

        log.warning(f"Native token {currency_upper} not found in mapping for {env}")
        return None

    # 2. Для токенов с контрактом - ищем в Fireblocks по contractAddress или issuerAddress
    contract_lower = contract_address.lower()
    
    log.debug(f"Searching for {currency_upper} with contract {contract_address}")

    for fb_asset in fb_assets:
        asset_id = fb_asset.get("id", "")
        # Fireblocks может хранить адрес в contractAddress или issuerAddress
        fb_contract_addr = fb_asset.get("contractAddress", "")
        fb_issuer_addr = fb_asset.get("issuerAddress", "")

        # Проверяем contractAddress
        if fb_contract_addr and fb_contract_addr.lower() == contract_lower:
            log.debug(
                f"Mapped {currency_upper} via contractAddress -> {asset_id}"
            )
            return asset_id

        # Проверяем issuerAddress
        if fb_issuer_addr and fb_issuer_addr.lower() == contract_lower:
            log.debug(
                f"Mapped {currency_upper} via issuerAddress -> {asset_id}"
            )
            return asset_id

    # Не нашли
    log.warning(
        f"Asset not found for {currency_upper} with contract {contract_address}"
    )
    return None


async def resolve_assets_to_fireblocks_ids(
    assets: list[dict],
    is_testnet: bool,
    fb_assets: list[dict],
) -> list[str]:
    """
    Преобразовать список {currency, contract_address} в Fireblocks asset IDs.

    Args:
        assets: Список [{currency, contract_address}, ...]
        is_testnet: True для testnet
        fb_assets: Список всех доступных Fireblocks assets

    Returns:
        Список Fireblocks asset IDs
    """
    resolved_ids = []
    
    for asset in assets:
        currency = asset.get("currency", "")
        contract_address = asset.get("contract_address", "")
        
        asset_id = await map_currency_to_fireblocks_asset(
            currency=currency,
            contract_address=contract_address,
            is_testnet=is_testnet,
            fb_assets=fb_assets,
        )
        
        if asset_id:
            resolved_ids.append(asset_id)
        else:
            log.warning(f"Could not resolve asset: {asset}")
    
    log.info(f"Resolved {len(resolved_ids)}/{len(assets)} assets to Fireblocks IDs")
    return resolved_ids
