"""Fireblocks provider - тонкая обёртка над FireblocksService."""

from app.services.custody.providers.base import BaseProvider
from app.services.custody.fireblocks import FireblocksService


class FireblocksProvider(BaseProvider):
    def __init__(self):
        # Ленивая инициализация: сам сервис (и требование API-ключей) нужен только
        # при реальных вызовах к Fireblocks, а не при создании обёртки-провайдера.
        self.__service: FireblocksService | None = None

    @property
    def _service(self) -> FireblocksService:
        if self.__service is None:
            self.__service = FireblocksService()
        return self.__service

    @property
    def provider_name(self) -> str:
        return "fireblocks"

    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        return await self._service.create_vault(name, auto_fuel)

    async def get_vault(self, vault_id: str) -> dict:
        return await self._service.get_vault(vault_id)

    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        return await self._service.get_vaults(name_prefix=name_prefix)

    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        return await self._service.activate_asset(vault_id, asset_id)

    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        return await self._service.get_asset_balance(vault_id, asset_id)

    async def get_vault_balance(self, vault_id: str) -> dict:
        return await self._service.get_vault_balance(vault_id)

    async def get_vault_asset_info(self, vault_id: str, asset_id: str) -> dict | None:
        return await self._service.get_vault_asset_info(vault_id, asset_id)

    async def find_asset_by_contract_or_currency(
        self, currency: str, contract_address: str | None = None, is_testnet: bool = False
    ) -> dict | None:
        return await self._service.find_asset_by_contract_or_currency(
            currency, contract_address, is_testnet
        )

    async def create_transaction(self, data: dict) -> dict:
        return await self._service.create_transaction(data)

    async def get_transaction(self, tx_id: str) -> dict:
        return await self._service.get_transaction(tx_id)

    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        return await self._service.add_whitelist_address(
            vault_id, asset_id, address, description
        )

    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        return await self._service.get_whitelist_addresses(vault_id, asset_id)

    async def remove_whitelist_address(self, vault_id: str, whitelist_id: str) -> dict:
        return await self._service.remove_whitelist_address(vault_id, whitelist_id)

    async def get_supported_assets(self) -> list[dict]:
        return await self._service.get_supported_assets()

    # Webhooks (Fireblocks-specific)
    async def get_webhooks(self) -> list[dict]:
        return await self._service.get_webhooks()

    async def get_webhook(self, webhook_id: str) -> dict:
        return await self._service.get_webhook(webhook_id)

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        description: str | None = None,
        enabled: bool = True,
    ) -> dict:
        return await self._service.create_webhook(url, events, description, enabled)

    async def update_webhook(
        self,
        webhook_id: str,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        return await self._service.update_webhook(
            webhook_id, url, events, description, enabled
        )

    async def delete_webhook(self, webhook_id: str) -> dict:
        return await self._service.delete_webhook(webhook_id)
