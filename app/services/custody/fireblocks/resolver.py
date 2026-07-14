# AssetModel -> Fireblocks asset ID: по contract_address для токенов, по blockchain для нативных.
# Ключевая точка интеграции для provider-agnostic архитектуры.

from functools import lru_cache
from typing import TYPE_CHECKING

from app.config import log

if TYPE_CHECKING:
    from app.models import AssetModel


class FireblocksAssetResolver:
    # с кэшем чтобы не дёргать API повторно

    def __init__(self):
        self._cache: dict[str, str] = {}  # (blockchain, contract, testnet) -> fb_asset_id
        self._fb_assets: list[dict] | None = None
    
    async def _ensure_fb_assets(self) -> list[dict]:
        if self._fb_assets is None:
            from app.services.custody.fireblocks.service import fireblocks_service
            fb_service = fireblocks_service()
            self._fb_assets = await fb_service.get_supported_assets()
        return self._fb_assets
    
    def _make_cache_key(self, asset: "AssetModel") -> str:
        return f"{asset.blockchain}:{asset.contract_address or 'NATIVE'}:{asset.testnet or 'MAINNET'}"

    async def resolve(self, asset: "AssetModel") -> str | None:
        cache_key = self._make_cache_key(asset)

        if cache_key in self._cache:
            return self._cache[cache_key]

        from app.services.custody.fireblocks.service import fireblocks_service
        fb_service = fireblocks_service()
        
        is_testnet = asset.testnet is not None
        
        fb_asset = await fb_service.find_asset_by_contract_or_currency(
            currency=asset.symbol,
            contract_address=asset.contract_address,
            is_testnet=is_testnet,
        )
        
        if fb_asset:
            fb_id = fb_asset.get("id", "")
            self._cache[cache_key] = fb_id
            log.debug(f"Resolved {asset.symbol} -> {fb_id}")
            return fb_id
        
        log.warning(
            f"⚠️ Fireblocks asset not found for {asset.symbol} "
            f"(blockchain={asset.blockchain}, contract={asset.contract_address})"
        )
        return None
    
    async def resolve_by_contract(
        self,
        contract_address: str,
        blockchain: str,
        is_testnet: bool = False,
    ) -> str | None:
        # основной путь резолва токенов
        cache_key = f"{blockchain}:{contract_address}:{'TESTNET' if is_testnet else 'MAINNET'}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        fb_assets = await self._ensure_fb_assets()
        contract_lower = contract_address.lower()

        for fb_asset in fb_assets:
            fb_contract = fb_asset.get("contractAddress", "") or ""
            fb_issuer = fb_asset.get("issuerAddress", "") or ""

            if contract_lower in [fb_contract.lower(), fb_issuer.lower()]:
                fb_id = fb_asset.get("id", "")
                is_fb_testnet = any(
                    "TEST" in s.upper()
                    for s in [fb_id, str(fb_asset.get("type", "")), str(fb_asset.get("nativeAsset", ""))]
                )
                
                if is_fb_testnet == is_testnet:
                    self._cache[cache_key] = fb_id
                    return fb_id
        
        return None
    
    async def resolve_native(
        self,
        blockchain: str,
        symbol: str,
        is_testnet: bool = False,
    ) -> str | None:
        from app.services.custody.fireblocks.service import fireblocks_service
        from app.services.custody.fireblocks.utils import mapping_native_tokens

        cache_key = f"{blockchain}:NATIVE:{'TESTNET' if is_testnet else 'MAINNET'}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        # сначала пробуем маппинг
        env_key = "dev" if is_testnet else "prod"
        native_map = mapping_native_tokens().get(env_key, {}).get(symbol.upper())

        if native_map:
            fb_id = native_map.get("asset_id", "")
            if fb_id:
                self._cache[cache_key] = fb_id
                return fb_id

        # фолбэк на Fireblocks service
        fb_service = fireblocks_service()
        fb_asset = await fb_service.find_asset_by_contract_or_currency(
            currency=symbol,
            contract_address=None,
            is_testnet=is_testnet,
        )
        
        if fb_asset:
            fb_id = fb_asset.get("id", "")
            self._cache[cache_key] = fb_id
            return fb_id
        
        return None
    
    def clear_cache(self):
        self._cache.clear()
        self._fb_assets = None


_resolver: FireblocksAssetResolver | None = None


def get_resolver() -> FireblocksAssetResolver:
    global _resolver
    if _resolver is None:
        _resolver = FireblocksAssetResolver()
    return _resolver


async def resolve_fireblocks_asset(asset: "AssetModel") -> str | None:
    resolver = get_resolver()
    return await resolver.resolve(asset)

