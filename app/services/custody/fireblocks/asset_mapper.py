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
    # Fireblocks asset ID по currency + contract_address (пустой contract = нативный)
    currency_upper = currency.upper()

    # нативный токен - маппим через словарь
    if not contract_address:
        native_mapping = mapping_native_tokens()
        env = "dev" if is_testnet else "prod"

        if env in native_mapping and currency_upper in native_mapping[env]:
            asset_id = native_mapping[env][currency_upper]["asset_id"]
            log.debug(f"Mapped native token {currency_upper} -> {asset_id}")
            return asset_id

        log.warning(f"Native token {currency_upper} not found in mapping for {env}")
        return None

    # токены с контрактом - ищем по contractAddress или issuerAddress
    contract_lower = contract_address.lower()

    log.debug(f"Searching for {currency_upper} with contract {contract_address}")

    for fb_asset in fb_assets:
        asset_id = fb_asset.get("id", "")
        fb_contract_addr = fb_asset.get("contractAddress", "")
        fb_issuer_addr = fb_asset.get("issuerAddress", "")

        if fb_contract_addr and fb_contract_addr.lower() == contract_lower:
            log.debug(
                f"Mapped {currency_upper} via contractAddress -> {asset_id}"
            )
            return asset_id

        if fb_issuer_addr and fb_issuer_addr.lower() == contract_lower:
            log.debug(
                f"Mapped {currency_upper} via issuerAddress -> {asset_id}"
            )
            return asset_id

    log.warning(
        f"Asset not found for {currency_upper} with contract {contract_address}"
    )
    return None


async def resolve_assets_to_fireblocks_ids(
    assets: list[dict],
    is_testnet: bool,
    fb_assets: list[dict],
) -> list[str]:
    # список {currency, contract_address} -> Fireblocks asset IDs
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
