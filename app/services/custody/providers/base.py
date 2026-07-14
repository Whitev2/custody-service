"""Base provider interface for custody providers."""

from abc import ABC, abstractmethod
from typing import Protocol


class CustodyProvider(Protocol):
    """
    Protocol for custody providers.
    
    Allows switching between different providers (Fireblocks, custom, etc.)
    without changing business logic.
    """

    @abstractmethod
    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        """
        Create new vault account.
        
        Returns:
            {
                "id": "vault_id",
                "name": "vault_name",
                ...
            }
        """
        pass

    @abstractmethod
    async def get_vault(self, vault_id: str) -> dict:
        """Get vault account info."""
        pass

    @abstractmethod
    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        """Get vault accounts (optionally filtered by name prefix)."""
        pass

    @abstractmethod
    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        """
        Activate asset in vault (creates address).
        
        Returns:
            {
                "address": "0x...",
                "legacyAddress": "..." | None,
                "tag": "..." | None
            }
        """
        pass

    @abstractmethod
    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        """Get asset balance in vault."""
        pass

    @abstractmethod
    async def get_vault_balance(self, vault_id: str) -> dict:
        """Get all asset balances in vault."""
        pass

    @abstractmethod
    async def create_transaction(self, data: dict) -> dict:
        """
        Create transaction.
        
        Args:
            data: Transaction data with source, destination, amount, etc.
        
        Returns:
            {
                "id": "tx_id",
                "status": "PENDING",
                "txHash": "0x..." | None,
                ...
            }
        """
        pass

    @abstractmethod
    async def get_transaction(self, tx_id: str) -> dict:
        """Get transaction info."""
        pass

    @abstractmethod
    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        """Add address to whitelist."""
        pass

    @abstractmethod
    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        """Get whitelist addresses (optionally filtered by asset)."""
        pass

    @abstractmethod
    async def remove_whitelist_address(
        self, vault_id: str, whitelist_id: str
    ) -> dict:
        """Remove address from whitelist."""
        pass

    @abstractmethod
    async def get_supported_assets(self) -> list[dict]:
        """Get list of supported assets."""
        pass


class BaseProvider(ABC):
    """
    Base abstract class for custody providers.
    
    All provider implementations should inherit from this class.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider name (e.g., 'fireblocks', 'custom')."""
        pass

    @abstractmethod
    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        """Create new vault account."""
        pass

    @abstractmethod
    async def get_vault(self, vault_id: str) -> dict:
        """Get vault account info."""
        pass

    @abstractmethod
    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        """Get vault accounts (optionally filtered by name prefix)."""
        pass

    @abstractmethod
    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        """Activate asset in vault."""
        pass

    @abstractmethod
    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        """Get asset balance."""
        pass

    @abstractmethod
    async def get_vault_balance(self, vault_id: str) -> dict:
        """Get all balances in vault."""
        pass

    @abstractmethod
    async def create_transaction(self, data: dict) -> dict:
        """Create transaction."""
        pass

    @abstractmethod
    async def get_transaction(self, tx_id: str) -> dict:
        """Get transaction info."""
        pass

    @abstractmethod
    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        """Add address to whitelist."""
        pass

    @abstractmethod
    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        """Get whitelist addresses."""
        pass

    @abstractmethod
    async def remove_whitelist_address(
        self, vault_id: str, whitelist_id: str
    ) -> dict:
        """Remove address from whitelist."""
        pass

    @abstractmethod
    async def get_supported_assets(self) -> list[dict]:
        """Get supported assets."""
        pass

    # Optional methods (provider-specific)
    # These are not required but can be implemented for specific providers
    
    async def get_webhooks(self) -> list[dict]:
        """Get list of webhooks (optional, provider-specific)."""
        raise NotImplementedError("Webhook management not supported by this provider")
    
    async def get_webhook(self, webhook_id: str) -> dict:
        """Get webhook info (optional, provider-specific)."""
        raise NotImplementedError("Webhook management not supported by this provider")
    
    async def create_webhook(
        self, url: str, events: list[str], description: str | None = None, enabled: bool = True
    ) -> dict:
        """Create webhook (optional, provider-specific)."""
        raise NotImplementedError("Webhook management not supported by this provider")
    
    async def update_webhook(
        self, webhook_id: str, url: str | None = None, events: list[str] | None = None,
        description: str | None = None, enabled: bool | None = None
    ) -> dict:
        """Update webhook (optional, provider-specific)."""
        raise NotImplementedError("Webhook management not supported by this provider")
    
    async def delete_webhook(self, webhook_id: str) -> dict:
        """Delete webhook (optional, provider-specific)."""
        raise NotImplementedError("Webhook management not supported by this provider")
