"""Fireblocks provider implementation."""

from app.services.custody.providers.base import BaseProvider
from app.services.custody.fireblocks import FireblocksService


class FireblocksProvider(BaseProvider):
    """Fireblocks custody provider implementation."""

    def __init__(self):
        """Initialize Fireblocks provider."""
        self._service = FireblocksService()

    @property
    def provider_name(self) -> str:
        """Provider name."""
        return "fireblocks"

    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        """Create new vault account."""
        return await self._service.create_vault(name, auto_fuel)

    async def get_vault(self, vault_id: str) -> dict:
        """Get vault account info."""
        return await self._service.get_vault(vault_id)

    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        """Get vault accounts (optionally filtered by name prefix)."""
        return await self._service.get_vaults(name_prefix=name_prefix)

    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        """Activate asset in vault."""
        return await self._service.activate_asset(vault_id, asset_id)

    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        """Get asset balance."""
        return await self._service.get_asset_balance(vault_id, asset_id)

    async def get_vault_balance(self, vault_id: str) -> dict:
        """Get all balances in vault."""
        return await self._service.get_vault_balance(vault_id)
    
    async def get_vault_asset_info(self, vault_id: str, asset_id: str) -> dict | None:
        """Get asset info with address from vault."""
        return await self._service.get_vault_asset_info(vault_id, asset_id)

    async def find_asset_by_contract_or_currency(
        self, currency: str, contract_address: str | None = None, is_testnet: bool = False
    ) -> dict | None:
        """Find asset by contract address or currency for native tokens."""
        return await self._service.find_asset_by_contract_or_currency(
            currency, contract_address, is_testnet
        )

    async def create_transaction(self, data: dict) -> dict:
        """Create transaction."""
        return await self._service.create_transaction(data)

    async def get_transaction(self, tx_id: str) -> dict:
        """Get transaction info."""
        return await self._service.get_transaction(tx_id)

    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        """Add address to whitelist."""
        return await self._service.add_whitelist_address(
            vault_id, asset_id, address, description
        )

    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        """Get whitelist addresses."""
        return await self._service.get_whitelist_addresses(vault_id, asset_id)

    async def remove_whitelist_address(self, vault_id: str, whitelist_id: str) -> dict:
        """Remove address from whitelist."""
        return await self._service.remove_whitelist_address(vault_id, whitelist_id)

    async def get_supported_assets(self) -> list[dict]:
        """Get supported assets."""
        return await self._service.get_supported_assets()

    # Webhook management (Fireblocks-specific)
    async def get_webhooks(self) -> list[dict]:
        """Get list of webhooks."""
        return await self._service.get_webhooks()

    async def get_webhook(self, webhook_id: str) -> dict:
        """Get webhook info."""
        return await self._service.get_webhook(webhook_id)

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        description: str | None = None,
        enabled: bool = True,
    ) -> dict:
        """Create webhook."""
        return await self._service.create_webhook(url, events, description, enabled)

    async def update_webhook(
        self,
        webhook_id: str,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        """Update webhook."""
        return await self._service.update_webhook(
            webhook_id, url, events, description, enabled
        )

    async def delete_webhook(self, webhook_id: str) -> dict:
        """Delete webhook."""
        return await self._service.delete_webhook(webhook_id)
