"""Шаблон кастомного провайдера - реализуй под своё решение (HSM, ноды, etc.)."""

from app.services.custody.providers.base import BaseProvider
from app.config import log


class CustomProvider(BaseProvider):
    """Шаблон кастомного custody-провайдера."""

    def __init__(self):
        log.info("Initializing custom custody provider")

    @property
    def provider_name(self) -> str:
        return "custom"

    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        # TODO: Implement vault creation
        raise NotImplementedError("Custom provider not implemented yet")

    async def get_vault(self, vault_id: str) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def get_vault_balance(self, vault_id: str) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def create_transaction(self, data: dict) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def get_transaction(self, tx_id: str) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def remove_whitelist_address(self, vault_id: str, whitelist_id: str) -> dict:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")

    async def get_supported_assets(self) -> list[dict]:
        # TODO: Implement
        raise NotImplementedError("Custom provider not implemented yet")
