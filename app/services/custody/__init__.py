"""Custody service - управление custody провайдерами и интеграциями."""

# Factory - фабрика для создания провайдеров
from app.services.custody.factory import (
    create_provider,
    get_provider,
    set_provider,
)

# Providers - реализации провайдеров
from app.services.custody.providers import (
    BaseProvider,
    CustodyProvider,
    FireblocksProvider,
    CustomProvider,
)

# Fireblocks - специфичная логика Fireblocks
from app.services.custody.fireblocks import (
    FireblocksService,
    fireblocks_service,
    sync_fireblocks_assets,
    mapping_native_tokens,
    parse_fireblocks_asset,
)

__all__ = [
    # Factory
    "create_provider",
    "get_provider",
    "set_provider",
    # Providers
    "BaseProvider",
    "CustodyProvider",
    "FireblocksProvider",
    "CustomProvider",
    # Fireblocks
    "FireblocksService",
    "fireblocks_service",
    "sync_fireblocks_assets",
    "mapping_native_tokens",
    "parse_fireblocks_asset",
]
