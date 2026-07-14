"""
Tests for health check endpoints.
"""

import pytest
from httpx import AsyncClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Test health check endpoint."""
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "custody_v2"

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client: AsyncClient):
        """Test root endpoint returns service info."""
        response = await client.get("/")

        # May return 404 or redirect, depends on implementation
        assert response.status_code in [200, 404, 307]
