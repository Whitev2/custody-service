"""
Integration tests with real Fireblocks API (Sandbox).

These tests make REAL API calls to Fireblocks Sandbox.

Run only integration tests:
    uv run pytest tests/test_fireblocks_integration.py -v -s

Run with output:
    uv run pytest tests/test_fireblocks_integration.py -v -s --tb=short

Requirements:
- API_KEY environment variable OR
- custody_v2/secrets/fireblocks.key file with private key
"""
import os
import pytest
from uuid import uuid4

# Mark all tests in this module as integration tests
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]


def has_fireblocks_credentials():
    """Check if Fireblocks credentials are available."""
    api_key = os.getenv("API_KEY")
    key_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "secrets", "fireblocks.key")
    return bool(api_key) or os.path.exists(key_file)


@pytest.mark.skipif(not has_fireblocks_credentials(), reason="Fireblocks credentials not configured")
class TestFireblocksVaultOperations:
    """Test real Fireblocks vault operations."""

    @pytest.fixture
    def fireblocks_service(self):
        """Get real Fireblocks service."""
        from app.services.custody import FireblocksService
        return FireblocksService()

    async def test_get_supported_assets(self, fireblocks_service):
        """Test fetching supported assets from Fireblocks."""
        assets = await fireblocks_service.get_supported_assets()
        
        assert isinstance(assets, list)
        assert len(assets) > 0
        
        # Check asset structure
        first_asset = assets[0]
        assert "id" in first_asset
        assert "name" in first_asset
        
        print(f"✅ Fetched {len(assets)} supported assets")
        
        # Print some testnet assets
        testnet_assets = [a for a in assets if "TEST" in a.get("id", "")]
        print(f"📋 Testnet assets: {[a['id'] for a in testnet_assets[:10]]}")

    async def test_get_vaults(self, fireblocks_service):
        """Test fetching vault list from Fireblocks."""
        vaults = await fireblocks_service.get_vaults()
        
        assert isinstance(vaults, list)
        print(f"✅ Fetched {len(vaults)} vaults")
        
        for vault in vaults[:5]:
            print(f"  - Vault {vault.get('id')}: {vault.get('name')}")

    async def test_create_vault(self, fireblocks_service):
        """Test creating a new vault in Fireblocks."""
        vault_name = f"TEST_VAULT_{uuid4().hex[:8].upper()}"
        
        result = await fireblocks_service.create_vault(vault_name, auto_fuel=True)
        
        assert "id" in result
        assert result.get("name") == vault_name
        
        print(f"✅ Created vault: ID={result['id']}, name={vault_name}")
        
        return result["id"]

    async def test_get_vault_info(self, fireblocks_service):
        """Test getting vault info."""
        # First get list of vaults
        vaults = await fireblocks_service.get_vaults()
        
        if not vaults:
            pytest.skip("No vaults exist to test")
        
        vault_id = vaults[0]["id"]
        vault_info = await fireblocks_service.get_vault(vault_id)
        
        assert "id" in vault_info
        assert "name" in vault_info
        
        print(f"✅ Got vault info: {vault_info.get('name')}")

    async def test_activate_testnet_asset(self, fireblocks_service):
        """Test activating a testnet asset in vault."""
        # Get or create vault
        vaults = await fireblocks_service.get_vaults()
        
        if not vaults:
            # Create new vault
            vault_name = f"ASSET_TEST_{uuid4().hex[:6].upper()}"
            vault = await fireblocks_service.create_vault(vault_name)
            vault_id = vault["id"]
        else:
            vault_id = vaults[0]["id"]
        
        # Activate ETH_TEST5 (Sepolia)
        result = await fireblocks_service.activate_asset(vault_id, "ETH_TEST5")
        
        assert "address" in result
        assert result["address"].startswith("0x")
        
        print(f"✅ Activated ETH_TEST5: address={result['address']}")

    async def test_activate_btc_testnet(self, fireblocks_service):
        """Test activating BTC testnet asset."""
        vaults = await fireblocks_service.get_vaults()
        
        if not vaults:
            pytest.skip("No vaults exist")
        
        vault_id = vaults[0]["id"]
        
        try:
            result = await fireblocks_service.activate_asset(vault_id, "BTC_TEST")
            
            assert "address" in result
            print(f"✅ Activated BTC_TEST: address={result['address']}")
        except Exception as e:
            if "already exists" in str(e).lower():
                print("⚠️ BTC_TEST already activated in this vault")
            else:
                raise

    async def test_get_asset_balance(self, fireblocks_service):
        """Test getting asset balance."""
        vaults = await fireblocks_service.get_vaults()
        
        if not vaults:
            pytest.skip("No vaults exist")
        
        vault_id = vaults[0]["id"]
        
        # Try to get ETH_TEST5 balance
        try:
            balance = await fireblocks_service.get_asset_balance(vault_id, "ETH_TEST5")
            
            print(f"✅ ETH_TEST5 balance: {balance}")
            assert "total" in balance or "balance" in balance or "available" in balance
        except Exception as e:
            if "not found" in str(e).lower() or "not activated" in str(e).lower():
                print("⚠️ Asset not activated in vault")
            else:
                raise


@pytest.mark.skipif(not has_fireblocks_credentials(), reason="Fireblocks credentials not configured")
class TestFireblocksWhitelist:
    """Test Fireblocks whitelist operations."""

    @pytest.fixture
    def fireblocks_service(self):
        """Get real Fireblocks service."""
        from app.services.custody import FireblocksService
        return FireblocksService()

    @pytest.mark.skip(reason="Whitelist API not fully supported in Sandbox mode")
    async def test_get_whitelist_addresses(self, fireblocks_service):
        """Test getting whitelist addresses."""
        vaults = await fireblocks_service.get_vaults()
        
        if not vaults:
            pytest.skip("No vaults exist")
        
        vault_id = vaults[0]["id"]
        
        addresses = await fireblocks_service.get_whitelist_addresses(vault_id)
        
        assert isinstance(addresses, list)
        print(f"✅ Whitelist has {len(addresses)} addresses")
        
        for addr in addresses[:5]:
            print(f"  - {addr.get('address', 'N/A')[:20]}... ({addr.get('asset', 'N/A')})")


@pytest.mark.skipif(not has_fireblocks_credentials(), reason="Fireblocks credentials not configured")
class TestFireblocksFullFlow:
    """Test complete flow with Fireblocks."""

    @pytest.fixture
    def fireblocks_service(self):
        """Get real Fireblocks service."""
        from app.services.custody import FireblocksService
        return FireblocksService()

    @pytest.mark.slow
    async def test_full_vault_creation_flow(self, fireblocks_service):
        """
        Test complete vault creation flow:
        1. Create vault
        2. Activate testnet assets
        3. Get addresses
        4. Get balances
        """
        print("\n🚀 Starting full vault creation flow...")
        
        # 1. Create vault
        vault_name = f"FLOW_TEST_{uuid4().hex[:6].upper()}"
        vault = await fireblocks_service.create_vault(vault_name, auto_fuel=True)
        vault_id = vault["id"]
        print(f"✅ Step 1: Created vault {vault_id} ({vault_name})")
        
        # 2. Activate assets
        assets_to_activate = ["ETH_TEST5", "BTC_TEST", "SOL_TEST"]
        activated = []
        
        for asset_id in assets_to_activate:
            try:
                result = await fireblocks_service.activate_asset(vault_id, asset_id)
                activated.append({
                    "asset": asset_id,
                    "address": result.get("address"),
                })
                print(f"✅ Step 2: Activated {asset_id}: {result.get('address', 'N/A')[:30]}...")
            except Exception as e:
                print(f"⚠️ Could not activate {asset_id}: {str(e)[:50]}")
        
        assert len(activated) > 0, "At least one asset should be activated"
        
        # 3. Get vault info with balances
        vault_info = await fireblocks_service.get_vault(vault_id)
        print(f"✅ Step 3: Got vault info: {vault_info.get('name')}")
        
        # 4. Check balances
        for asset in activated:
            try:
                balance = await fireblocks_service.get_asset_balance(vault_id, asset["asset"])
                print(f"✅ Step 4: {asset['asset']} balance = {balance.get('total', balance.get('available', '0'))}")
            except Exception as e:
                print(f"⚠️ Could not get {asset['asset']} balance: {str(e)[:30]}")
        
        print(f"\n🎉 Full flow completed! Vault: {vault_name}")
        
        return {
            "vault_id": vault_id,
            "vault_name": vault_name,
            "activated_assets": activated,
        }


@pytest.mark.skipif(not has_fireblocks_credentials(), reason="Fireblocks credentials not configured")
class TestFireblocksAPIEndpoints:
    """
    Test custody_v2 API endpoints with real Fireblocks.
    
    These tests require:
    - Fireblocks credentials
    - Running PostgreSQL (use integration_client fixture)
    
    To run these tests with DB:
        docker compose up pg_custody_v2 -d
        API_KEY=xxx uv run pytest tests/test_fireblocks_integration.py::TestFireblocksAPIEndpoints -v -s
    """

    @pytest.fixture
    async def client(self, integration_session):
        """Create test client with SQLite database."""
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        from app.storage.database import get_db
        
        async def override_get_db():
            yield integration_session
        
        app.dependency_overrides[get_db] = override_get_db
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        
        app.dependency_overrides.clear()

    async def test_api_sync_assets(self, client):
        """Test syncing assets via API."""
        response = await client.post("/admin/sync-assets")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["status"] == "success"
        assert data["total_fireblocks"] > 0
        
        print(f"✅ Synced assets: {data['created']} created, {data['updated']} updated")

    async def test_api_create_vault_with_assets(self, client):
        """Test creating vault with assets via API."""
        # First sync assets
        await client.post("/admin/sync-assets")
        
        # Create vault with ETH Sepolia
        response = await client.post(
            "/vault/create",
            json={
                "name": f"API_TEST_{uuid4().hex[:6].upper()}",
                "assets": [
                    {"blockchain": "ETHEREUM", "currency": "ETH", "network": "SEPOLIA"}
                ],
                "auto_fuel": True,
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert "vault_id" in data
        assert data["name"].startswith("API_TEST_")
        assert len(data["wallets"]) == 1
        assert data["wallets"][0]["address"].startswith("0x")
        
        print(f"✅ Created vault via API: {data['name']}")
        print(f"   Address: {data['wallets'][0]['address']}")

    async def test_api_list_vaults(self, client):
        """Test listing vaults via API."""
        response = await client.get("/vault/list")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "vaults" in data
        assert "total" in data
        
        print(f"✅ Listed {data['total']} vaults via API")

    async def test_api_get_vault_info(self, client):
        """Test getting vault info via API."""
        # First create a vault to ensure we have one
        await client.post("/admin/sync-assets")
        create_response = await client.post(
            "/vault/create",
            json={
                "name": f"INFO_TEST_{uuid4().hex[:6].upper()}",
                "assets": [],
                "auto_fuel": True,
            },
        )
        
        if create_response.status_code != 200:
            pytest.skip("Could not create test vault")
        
        vault_id = create_response.json()["vault_id"]
        
        response = await client.get(f"/vault/{vault_id}/info")
        
        assert response.status_code == 200
        data = response.json()
        
        assert str(data["vault_id"]) == str(vault_id)
        print(f"✅ Got vault info: {data['name']}, {len(data['wallets'])} wallets")
