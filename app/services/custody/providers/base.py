from abc import ABC, abstractmethod
from typing import Protocol


class CustodyProvider(Protocol):
    """Общий интерфейс провайдеров (Fireblocks, custom, ...)."""

    @abstractmethod
    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        pass

    @abstractmethod
    async def get_vault(self, vault_id: str) -> dict:
        pass

    @abstractmethod
    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        pass

    @abstractmethod
    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        pass

    @abstractmethod
    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        pass

    @abstractmethod
    async def get_vault_balance(self, vault_id: str) -> dict:
        pass

    @abstractmethod
    async def create_transaction(self, data: dict) -> dict:
        pass

    @abstractmethod
    async def get_transaction(self, tx_id: str) -> dict:
        pass

    @abstractmethod
    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        pass

    @abstractmethod
    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        pass

    @abstractmethod
    async def remove_whitelist_address(
        self, vault_id: str, whitelist_id: str
    ) -> dict:
        pass

    @abstractmethod
    async def get_supported_assets(self) -> list[dict]:
        pass


class BaseProvider(ABC):
    """Базовый класс для реализаций провайдеров."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @abstractmethod
    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        pass

    @abstractmethod
    async def get_vault(self, vault_id: str) -> dict:
        pass

    @abstractmethod
    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        pass

    @abstractmethod
    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        pass

    @abstractmethod
    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        pass

    @abstractmethod
    async def get_vault_balance(self, vault_id: str) -> dict:
        pass

    @abstractmethod
    async def create_transaction(self, data: dict) -> dict:
        pass

    @abstractmethod
    async def get_transaction(self, tx_id: str) -> dict:
        pass

    @abstractmethod
    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        pass

    @abstractmethod
    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        pass

    @abstractmethod
    async def remove_whitelist_address(
        self, vault_id: str, whitelist_id: str
    ) -> dict:
        pass

    @abstractmethod
    async def get_supported_assets(self) -> list[dict]:
        pass

    # Опциональные методы (зависят от провайдера)

    async def get_webhooks(self) -> list[dict]:
        raise NotImplementedError("Webhook management not supported by this provider")

    async def get_webhook(self, webhook_id: str) -> dict:
        raise NotImplementedError("Webhook management not supported by this provider")

    async def create_webhook(
        self, url: str, events: list[str], description: str | None = None, enabled: bool = True
    ) -> dict:
        raise NotImplementedError("Webhook management not supported by this provider")

    async def update_webhook(
        self, webhook_id: str, url: str | None = None, events: list[str] | None = None,
        description: str | None = None, enabled: bool | None = None
    ) -> dict:
        raise NotImplementedError("Webhook management not supported by this provider")

    async def delete_webhook(self, webhook_id: str) -> dict:
        raise NotImplementedError("Webhook management not supported by this provider")
