"""Tests for storage configuration models."""
import pytest
from pathlib import Path

from oceanstream.storage.config import (
    StorageProvider,
    LocalStorageConfig,
    AzureStorageConfig,
    S3StorageConfig,
    GCSStorageConfig,
    StorageConfiguration,
)


class TestStorageProvider:
    """Tests for StorageProvider enum."""
    
    def test_storage_provider_values(self):
        """Test that all expected provider types exist."""
        assert StorageProvider.LOCAL == "local"
        assert StorageProvider.AZURE == "azure"
        assert StorageProvider.S3 == "s3"
        assert StorageProvider.GCS == "gcs"


class TestLocalStorageConfig:
    """Tests for LocalStorageConfig."""
    
    def test_create_local_config_with_path(self):
        """Test creating local config with base path."""
        config = LocalStorageConfig(
            provider="local",
            base_path=Path("/data/output"),
        )
        
        assert config.provider == "local"
        assert config.base_path == Path("/data/output")
    
    def test_local_config_to_dict(self):
        """Test serialization to dict."""
        config = LocalStorageConfig(
            provider="local",
            base_path=Path("/data/output"),
        )
        
        data = config.to_dict()
        
        assert data["provider"] == "local"
        assert data["base_path"] == "/data/output"
    
    def test_local_config_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "provider": "local",
            "base_path": "/data/output",
        }
        
        config = LocalStorageConfig.from_dict(data)
        
        assert config.provider == "local"
        assert config.base_path == Path("/data/output")


class TestAzureStorageConfig:
    """Tests for AzureStorageConfig."""
    
    def test_create_azure_config_with_connection_string(self):
        """Test creating Azure config with connection string."""
        config = AzureStorageConfig(
            provider="azure",
            container_name="mycontainer",
            connection_string="DefaultEndpointsProtocol=https;...",
        )
        
        assert config.provider == "azure"
        assert config.container_name == "mycontainer"
        assert config.connection_string == "DefaultEndpointsProtocol=https;..."
    
    def test_azure_config_to_dict(self):
        """Test serialization to dict."""
        config = AzureStorageConfig(
            provider="azure",
            container_name="mycontainer",
            connection_string="conn-string",
        )
        
        data = config.to_dict()
        
        assert data["provider"] == "azure"
        assert data["container_name"] == "mycontainer"
    
    def test_azure_config_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "provider": "azure",
            "container_name": "mycontainer",
            "connection_string": "conn-string",
            "account_name": None,
            "account_key": None,
        }
        
        config = AzureStorageConfig.from_dict(data)
        
        assert config.provider == "azure"
        assert config.container_name == "mycontainer"


class TestStorageConfiguration:
    """Tests for StorageConfiguration."""
    
    def test_create_empty_configuration(self):
        """Test creating empty configuration."""
        config = StorageConfiguration()
        
        assert config.providers == {}
        assert config.active_provider is None
    
    def test_add_provider(self):
        """Test adding a provider."""
        storage_config = StorageConfiguration()
        local_config = LocalStorageConfig(provider="local", base_path=Path("/data"))
        
        storage_config.add_provider("local", local_config)
        
        assert "local" in storage_config.providers
    
    def test_set_active_provider(self):
        """Test setting active provider."""
        storage_config = StorageConfiguration()
        local_config = LocalStorageConfig(provider="local")
        
        storage_config.add_provider("local", local_config)
        
        assert storage_config.active_provider == "local"
    
    def test_get_active_config(self):
        """Test getting active configuration."""
        storage_config = StorageConfiguration()
        local_config = LocalStorageConfig(provider="local", base_path=Path("/data"))
        
        storage_config.add_provider("local", local_config)
        
        name, config = storage_config.get_active_config()
        
        assert name == "local"
        assert config == local_config
    
    def test_to_dict(self):
        """Test serialization to dict."""
        storage_config = StorageConfiguration()
        local_config = LocalStorageConfig(provider="local", base_path=Path("/data"))
        
        storage_config.add_provider("local", local_config)
        
        data = storage_config.to_dict()
        
        assert "providers" in data
        assert "active_provider" in data
        assert data["active_provider"] == "local"
    
    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "providers": {
                "local": {
                    "provider": "local",
                    "base_path": "/data",
                }
            },
            "active_provider": "local",
        }
        
        storage_config = StorageConfiguration.from_dict(data)
        
        assert "local" in storage_config.providers
        assert storage_config.active_provider == "local"
