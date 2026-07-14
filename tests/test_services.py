"""
Tests for services.
"""

import pytest
from unittest.mock import MagicMock

from app.services.custody import (
    create_provider,
    get_provider,
    set_provider,
    FireblocksProvider,
    CustomProvider,
)


class TestProviderFactory:
    """Tests for provider factory."""

    def test_create_fireblocks_provider(self):
        """Test creating Fireblocks provider."""
        provider = create_provider("fireblocks")
        assert isinstance(provider, FireblocksProvider)
        assert provider.provider_name == "fireblocks"

    def test_create_custom_provider(self):
        """Test creating custom provider."""
        provider = create_provider("custom")
        assert isinstance(provider, CustomProvider)
        assert provider.provider_name == "custom"

    def test_create_provider_case_insensitive(self):
        """Test that provider name is case insensitive."""
        provider = create_provider("FIREBLOCKS")
        assert isinstance(provider, FireblocksProvider)

        provider = create_provider("Fireblocks")
        assert isinstance(provider, FireblocksProvider)

    def test_invalid_provider_raises(self):
        """Test that invalid provider raises error."""
        with pytest.raises(ValueError) as exc_info:
            create_provider("invalid_provider")

        assert "Unsupported provider" in str(exc_info.value)

    def test_set_provider(self):
        """Test setting custom provider."""
        mock_provider = MagicMock()
        mock_provider.provider_name = "mock"

        set_provider(mock_provider)

        provider = get_provider()
        assert provider == mock_provider
        assert provider.provider_name == "mock"

        # Reset provider
        set_provider(None)
