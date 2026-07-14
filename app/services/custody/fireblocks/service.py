"""Fireblocks API service."""

import time
import json
import hashlib

from urllib.parse import urlparse
from urllib.parse import quote

import jwt

from app.config import cfg, log
from app.services.http_client import http_client
from app.services.custody.fireblocks.utils import (
    parse_fireblocks_asset,
    mapping_native_tokens,
)


class FireblocksService:
    """Service for interacting with Fireblocks API."""

    def __init__(self):
        """Initialize Fireblocks service."""
        self.sandbox = cfg.fireblocks.SANDBOX
        self.base_url = (
            cfg.fireblocks.SANDBOX_URL
            if self.sandbox
            else cfg.fireblocks.PRODUCTION_URL
        )
        self.api_key = cfg.app.API_KEY
        self.private_key = cfg.app.PRIVATE_KEY

        # Allow local mode without keys
        if not self.api_key or not self.private_key:
            raise ValueError("API_KEY and PRIVATE_KEY must be set")

        log.info(f"✅ Fireblocks service initialized (sandbox={self.sandbox})")

    def _create_jwt(self, path: str, body_json: str = "") -> str:
        """Create JWT token for Fireblocks API authentication."""
        timestamp = int(time.time())
        nonce = int(time.time() * 1000)  # Use milliseconds to avoid collisions

        body_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()

        token_payload = {
            "uri": path,
            "nonce": nonce,
            "iat": timestamp,
            "exp": timestamp + 55,
            "sub": self.api_key,
            "bodyHash": body_hash,
        }

        return jwt.encode(token_payload, self.private_key, algorithm="RS256")

    async def _request(
        self, method: str, path: str, data: dict | None = None
    ) -> dict | list:
        """
        Make HTTP request to Fireblocks API.

        Returns:
            dict or list - depending on API response format
            Most endpoints return dict, but some (like /v1/supported_assets) return list
        """
        url = f"{self.base_url}{path}"

        # Serialize body manually to ensure hash matches sent data
        body_json = ""
        if data is not None:
            body_json = json.dumps(data, separators=(",", ":"))

        jwt_token = self._create_jwt(path, body_json)

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {jwt_token}",
            "X-API-Key": self.api_key,
        }

        request_kwargs = {
            "method": method,
            "url": url,
            "headers": headers,
        }

        if body_json:
            headers["content-type"] = "application/json"
            request_kwargs["data"] = body_json

        session = http_client.get_session()
        try:
            async with session.request(**request_kwargs) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    log.error(f"Fireblocks API error ({response.status}): {error_text}")
                    raise Exception(
                        f"Fireblocks API error ({response.status}): {error_text}"
                    )

                return await response.json()
        except Exception as e:
            log.error(f"Fireblocks request error: {e}")
            raise

    # ==================== Vault Management ====================

    async def create_vault(self, name: str, auto_fuel: bool = True) -> dict:
        """Create new vault account."""
        data = {"name": name, "autoFuel": auto_fuel}
        log.info(f"Creating vault: {name}")
        result = await self._request("POST", "/v1/vault/accounts", data)
        log.info(f"✅ Vault created: ID={result.get('id')}, name={name}")
        return result

    async def get_vault(self, vault_id: str) -> dict:
        """Get vault account info."""
        return await self._request("GET", f"/v1/vault/accounts/{vault_id}")

    async def get_vaults(self, name_prefix: str | None = None) -> list[dict]:
        """Get vault accounts (optionally filtered by name prefix)."""
        accounts: list[dict] = []
        next_path: str | None = "/v1/vault/accounts_paged"
        name_prefix_q: str | None = None
        limit_q = "500"
        if name_prefix:
            name_prefix_q = quote(name_prefix)
            next_path = (
                f"/v1/vault/accounts_paged?limit={limit_q}&namePrefix={name_prefix_q}"
            )
        else:
            next_path = f"/v1/vault/accounts_paged?limit={limit_q}"
        seen_paths: set[str] = set()

        while next_path:
            if next_path in seen_paths:
                break
            seen_paths.add(next_path)

            result = await self._request("GET", next_path)
            if not isinstance(result, dict):
                break

            accounts.extend(result.get("accounts", []) or [])

            next_url = result.get("nextUrl")
            if not next_url:
                paging = result.get("paging") or {}
                after = paging.get("after")
                if after:
                    if name_prefix_q:
                        params = [
                            f"limit={limit_q}",
                            f"namePrefix={name_prefix_q}",
                            f"after={after}",
                        ]
                        next_url = f"{self.base_url}/v1/vault/accounts_paged?{'&'.join(params)}"
                    else:
                        next_url = f"{self.base_url}/v1/vault/accounts_paged?limit={limit_q}&after={after}"

            if not next_url:
                next_path = None
                continue

            if isinstance(next_url, str) and next_url.startswith("http"):
                parsed = urlparse(next_url)
                next_path = parsed.path
                if parsed.query:
                    next_path = f"{next_path}?{parsed.query}"
            else:
                next_path = str(next_url)

        return accounts

    # ==================== Asset Management ====================

    async def activate_asset(self, vault_id: str, asset_id: str) -> dict:
        """Activate asset in vault (creates address)."""
        log.info(f"Activating asset {asset_id} in vault {vault_id}")
        result = await self._request(
            "POST", f"/v1/vault/accounts/{vault_id}/{asset_id}"
        )
        log.info(f"✅ Asset activated: address={result.get('address')}")
        return result

    async def get_asset_balance(self, vault_id: str, asset_id: str) -> dict:
        """Get asset balance in vault."""
        return await self._request("GET", f"/v1/vault/accounts/{vault_id}/{asset_id}")

    async def get_vault_balance(self, vault_id: str) -> dict:
        """Get all asset balances in vault."""
        return await self._request("GET", f"/v1/vault/accounts/{vault_id}")

    async def get_vault_asset_info(self, vault_id: str, asset_id: str) -> dict | None:
        """Get asset info with address from vault by fetching deposit addresses."""
        addresses = await self.get_deposit_addresses(vault_id, asset_id)
        if addresses and len(addresses) > 0:
            # Return first address with additional info
            first_addr = addresses[0]
            return {
                "id": asset_id,
                "address": first_addr.get("address"),
                "legacyAddress": first_addr.get("legacyAddress"),
                "tag": first_addr.get("tag"),
            }
        return None

    async def get_supported_assets(self) -> list[dict]:
        """
        Get list of supported assets.

        Fireblocks API returns list directly: [...]
        """
        result = await self._request("GET", "/v1/supported_assets")
        # Fireblocks API returns list directly
        if isinstance(result, list):
            return result
        # Fallback: if wrapped (shouldn't happen, but just in case)
        return result.get("assets", result.get("data", []))

    async def find_asset_by_contract_or_currency(
        self,
        currency: str,
        contract_address: str | None = None,
        is_testnet: bool = False,
    ) -> dict | None:
        """
        Найти asset в Fireblocks по contract_address или currency.

        Логика:
            - Если contract_address указан - ищем токен по contractAddress
            - Если contract_address = None - ищем нативный токен по currency (id или symbol)
            - Учитываем is_testnet для фильтрации тестовых/основных сетей

        Args:
            currency: Символ валюты (USDT, ETH, BTC)
            contract_address: Адрес контракта токена (None для нативных)
            is_testnet: True для тестовых сетей, False для mainnet

        Returns:
            dict с информацией об asset или None если не найден
        """
        assets = await self.get_supported_assets()

        def _is_asset_testnet(asset: dict) -> bool:
            """Определяем testnet по id/type/nativeAsset, т.к. type не всегда содержит TEST."""
            asset_id = str(asset.get("id", "")).upper()
            asset_type = str(asset.get("type", "")).upper()
            native_asset = str(asset.get("nativeAsset", "")).upper()
            return any(
                "TEST" in field for field in (asset_id, asset_type, native_asset)
            )

        if contract_address:
            # Ищем токен по contract address
            contract_lower = contract_address.lower()
            for asset in assets:
                asset_contract = asset.get("contractAddress", "") or ""
                issuer_address = asset.get("issuerAddress", "") or ""
                contract_matches = (
                    asset_contract.lower() == contract_lower
                    if asset_contract
                    else False
                )
                issuer_matches = (
                    issuer_address.lower() == contract_lower
                    if issuer_address
                    else False
                )
                if contract_matches or issuer_matches:
                    # Проверяем соответствие testnet/mainnet
                    is_asset_testnet = _is_asset_testnet(asset)
                    if is_asset_testnet == is_testnet:
                        parsed = (
                            parse_fireblocks_asset(asset.get("id", ""), asset) or {}
                        )
                        # Дополним недостающие поля, чтобы наверху были blockchain/type
                        asset = {
                            **asset,
                            "blockchain": parsed.get("blockchain"),
                            "network": parsed.get("network"),
                            "is_testnet": parsed.get("is_testnet", is_testnet),
                        }
                        return asset
        else:
            # Ищем нативный токен по currency
            currency_upper = currency.upper()
            native_map = (
                mapping_native_tokens()
                .get("dev" if is_testnet else "prod", {})
                .get(currency_upper)
            )

            # Сначала пытаемся взять asset по явному маппингу нативных токенов
            if native_map:
                mapped_asset_id = native_map.get("asset_id", "").upper()
                for asset in assets:
                    if str(asset.get("id", "")).upper() == mapped_asset_id:
                        parsed = (
                            parse_fireblocks_asset(mapped_asset_id, asset) or native_map
                        )
                        asset = {
                            **asset,
                            "blockchain": parsed.get("blockchain"),
                            "network": asset.get("type") or parsed.get("network"),
                            "is_testnet": parsed.get("is_testnet", is_testnet),
                        }
                        return asset

            for asset in assets:
                # Проверяем что это нативный токен (нет contractAddress)
                if not asset.get("contractAddress"):
                    asset_id = asset.get("id", "")

                    # Проверяем соответствие testnet/mainnet
                    is_asset_testnet = _is_asset_testnet(asset)
                    if is_asset_testnet != is_testnet:
                        continue

                    # Проверяем по id или по базовому названию
                    if (
                        asset_id == currency_upper
                        or asset_id.startswith(f"{currency_upper}_")
                        or asset.get("symbol", "").upper() == currency_upper
                    ):
                        parsed = parse_fireblocks_asset(asset_id, asset) or {}
                        asset = {
                            **asset,
                            "blockchain": parsed.get("blockchain"),
                            "network": parsed.get("network"),
                            "is_testnet": parsed.get("is_testnet", is_testnet),
                        }
                        return asset

        return None

    async def get_deposit_addresses(self, vault_id: str, asset_id: str) -> list[dict]:
        """
        Get all deposit addresses for asset in vault.

        Fireblocks API returns: {"addresses": [...]}
        """
        result = await self._request(
            "GET", f"/v1/vault/accounts/{vault_id}/{asset_id}/addresses"
        )
        # Fireblocks API returns {"addresses": [...]}
        if isinstance(result, list):
            return result
        return result.get("addresses", [])

    async def create_deposit_address(
        self, vault_id: str, asset_id: str, description: str = ""
    ) -> dict:
        """Create new deposit address for asset (if supported)."""
        data = {}
        if description:
            data["description"] = description
        return await self._request(
            "POST", f"/v1/vault/accounts/{vault_id}/{asset_id}/addresses", data
        )

    # ==================== Whitelist Management ====================

    async def add_whitelist_address(
        self, vault_id: str, asset_id: str, address: str, description: str = ""
    ) -> dict:
        """
        Add address to whitelist.

        Note: Whitelist is managed at vault level, but assetId is required in request body.
        """
        data = {
            "assetId": asset_id,
            "address": address,
        }
        if description:
            data["description"] = description
        return await self._request(
            "POST", f"/v1/vault/accounts/{vault_id}/whitelist", data
        )

    async def get_whitelist_addresses(
        self, vault_id: str, asset_id: str | None = None
    ) -> list[dict]:
        """
        Get whitelist addresses for vault.

        Args:
            vault_id: Vault ID
            asset_id: Optional asset ID to filter addresses

        Returns:
            List of whitelist addresses
        """
        result = await self._request("GET", f"/v1/vault/accounts/{vault_id}/whitelist")
        addresses = result.get("whitelist", [])

        # Filter by asset_id if provided
        if asset_id:
            addresses = [addr for addr in addresses if addr.get("assetId") == asset_id]

        return addresses

    async def remove_whitelist_address(self, vault_id: str, whitelist_id: str) -> dict:
        """Remove address from whitelist."""
        return await self._request(
            "DELETE", f"/v1/vault/accounts/{vault_id}/whitelist/{whitelist_id}"
        )

    # ==================== Transactions ====================

    async def create_transaction(self, data: dict) -> dict:
        """Create transaction in Fireblocks using Custody's own credentials."""
        return await self._request("POST", "/v1/transactions", data)

    async def create_transaction_with_jwt(
        self,
        jwt_token: str,
        api_key: str,
        transaction_body: dict,
    ) -> dict:
        """
        Create transaction in Fireblocks using JWT from Workflow.
        
        Workflow holds the SIGNER key and creates signed JWTs.
        Custody uses these JWTs to execute transactions.
        
        Args:
            jwt_token: Pre-signed JWT from Workflow (with SIGNER permissions)
            api_key: Workflow's API key (must match JWT 'sub' claim)
            transaction_body: The transaction payload (must match JWT bodyHash)
            
        Returns:
            Fireblocks transaction response
        """
        url = f"{self.base_url}/v1/transactions"
        
        # Use JWT from Workflow instead of creating our own
        body_json = json.dumps(transaction_body, separators=(",", ":"))
        
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {jwt_token}",
            "X-API-Key": api_key,  # Use Workflow's API key (matches JWT sub)
        }
        
        log.info(
            f"Creating transaction with Workflow JWT",
            extra={
                "asset_id": transaction_body.get("assetId"),
                "amount": transaction_body.get("amount"),
            }
        )
        
        session = http_client.get_session()
        try:
            async with session.post(url, headers=headers, data=body_json) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    log.error(f"Fireblocks API error ({response.status}): {error_text}")
                    raise Exception(
                        f"Fireblocks API error ({response.status}): {error_text}"
                    )
                return await response.json()
        except Exception as e:
            log.error(f"Error creating transaction with JWT: {e}", exc_info=True)
            raise

    async def get_transaction(self, tx_id: str) -> dict:
        """Get transaction info."""
        return await self._request("GET", f"/v1/transactions/{tx_id}")

    # ==================== Webhook Management ====================

    async def get_webhooks(self) -> list[dict]:
        """Get list of all webhooks."""
        result = await self._request("GET", "/v1/webhooks")
        return result.get("data", [])

    async def get_webhook(self, webhook_id: str) -> dict:
        """Get webhook info by ID."""
        return await self._request("GET", f"/v1/webhooks/{webhook_id}")

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        description: str | None = None,
        enabled: bool = True,
    ) -> dict:
        """Create new webhook."""
        data = {
            "url": url,
            "events": events,
            "enabled": enabled,
        }
        if description:
            data["description"] = description

        log.info(f"Creating webhook: url={url}, events={events}")
        result = await self._request("POST", "/v1/webhooks", data)
        log.info(f"✅ Webhook created: id={result.get('id')}")
        return result

    async def update_webhook(
        self,
        webhook_id: str,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        """Update webhook."""
        data = {}
        if url is not None:
            data["url"] = url
        if events is not None:
            data["events"] = events
        if description is not None:
            data["description"] = description
        if enabled is not None:
            data["enabled"] = enabled

        log.info(f"Updating webhook {webhook_id}: {data}")
        result = await self._request("PATCH", f"/v1/webhooks/{webhook_id}", data)
        log.info(f"✅ Webhook updated: id={webhook_id}")
        return result

    async def delete_webhook(self, webhook_id: str) -> dict:
        """Delete webhook."""
        log.info(f"Deleting webhook: {webhook_id}")
        result = await self._request("DELETE", f"/v1/webhooks/{webhook_id}")
        log.info(f"✅ Webhook deleted: id={webhook_id}")
        return result


# Global instance (deprecated - use provider factory instead)
# Lazy initialization to avoid errors during import
_fireblocks_service_instance: FireblocksService | None = None


def fireblocks_service() -> FireblocksService:
    """Get global Fireblocks service instance (lazy singleton)."""
    global _fireblocks_service_instance
    if _fireblocks_service_instance is None:
        _fireblocks_service_instance = FireblocksService()
    return _fireblocks_service_instance
