"""Tests for storage configuration manager."""
import pytest
from pathlib import Path
import json

from oceanstream.storage.manager import (
    get_storage_config_path,
    load_storage_configuration,
    save_storage_configuration,
    add_azure_storage,
    add_local_storage,
    get_active_storage_config,
    list_storage_providers,
    ENCRYPTED_FIELDS,
)
from oceanstream.storage.config import (
    StorageConfiguration,
    LocalStorageConfig,
    AzureStorageConfig,
)

@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Create a temporary home directory for testing."""
    test_home = tmp_path / "home"
    test_home.mkdir()
    monkeypatch.setenv("HOME", str(test_home))
    return test_home

@pytest.fixture
def clean_storage_config(temp_home):
    """Ensure clean storage config for each test."""
    config_path = get_storage_config_path()
    if config_path.exists():
        config_path.unlink()
    yield config_path
    if config_path.exists():
        config_path.unlink()

class TestGetStorageConfigPath:
    """Tests for get_storage_config_path."""
    
    def test_returns_path_in_oceanstream_dir(self, temp_home):
        """Test that config path is in ~/.oceanstream/."""
        config_path = get_storage_config_path()
        
        assert config_path.parent.name == ".oceanstream"
        assert config_path.name == "storage.json"

class TestLoadStorageConfiguration:
    """Tests for load_storage_configuration."""
    
    def test_load_nonexistent_file_returns_default(self, clean_storage_config):
        """Test loading when file doesn't exist raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_storage_configuration()

class TestSaveStorageConfiguration:
    """Tests for save_storage_configuration."""
    
    def test_save_creates_file(self, clean_storage_config):
        """Test that save creates the config file."""
        config = StorageConfiguration()
        local_config = LocalStorageConfig(provider="local")
        config.add_provider("local", local_config)
        
        save_storage_configuration(config)
        
        assert clean_storage_config.exists()
    
    def test_save_encrypts_sensitive_fields(self, clean_storage_config):
        """Test that sensitive fields are encrypted in saved file."""
        azure_config = AzureStorageConfig(
            provider="azure",
            connection_string="my-secret-connection-string",
        )
        config = StorageConfiguration()
        config.add_provider("azure", azure_config)
        
        save_storage_configuration(config)
        
        # Read the raw file
        saved_data = json.loads(clean_storage_config.read_text())
        
        # Encrypted fields should not contain plain text
        saved_conn_string = saved_data["providers"]["azure"]["connection_string"]
        
        assert saved_conn_string != "my-secret-connection-string"

class TestAddAzureStorage:
    """Tests for add_azure_storage helper."""
    
    def test_add_azure_with_connection_string(self, clean_storage_config):
        """Test adding Azure storage with connection string."""
        add_azure_storage(
            connection_string="my-connection-string",
            container_name="mycontainer",
        )
        
        config = load_storage_configuration()
        
        assert "azure" in config.providers
        assert config.providers["azure"].container_name == "mycontainer"
    
    def test_add_azure_requires_credentials(self, clean_storage_config):
        """Test that adding Azure without credentials raises error."""
        with pytest.raises(ValueError, match="connection_string OR"):
            add_azure_storage(
                container_name="container")

class TestAddLocalStorage:
    """Tests for add_local_storage helper."""
    
    def test_add_local_with_path(self, clean_storage_config):
        """Test adding local storage with path."""
        add_local_storage(
            base_path=Path("/data/output"),
        )
        
        config = load_storage_configuration()
        
        assert "local" in config.providers
        assert config.providers["local"].base_path == Path("/data/output")

class TestListStorageProviders:
    """Tests for list_storage_providers."""
    
    def test_list_returns_all_providers(self, clean_storage_config):
        """Test that list returns all configured providers."""
        add_local_storage()
        add_azure_storage(
            connection_string="conn",
        )
        
        providers = list_storage_providers()
        
        assert len(providers) == 2

class TestEncryptedFields:
    """Tests for ENCRYPTED_FIELDS constant."""
    
    def test_encrypted_fields_defined_for_all_providers(self):
        """Test that encrypted fields are defined for providers that need them."""
        assert "azure" in ENCRYPTED_FIELDS
        assert "s3" in ENCRYPTED_FIELDS
        assert "gcs" in ENCRYPTED_FIELDS
