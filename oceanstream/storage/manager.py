"""Storage configuration manager.

This module provides functions for loading, saving, and managing storage
configurations with automatic encryption of sensitive credentials.
"""

import json
from pathlib import Path
from typing import Optional

from .config import (
    StorageConfiguration,
    LocalStorageConfig,
    AzureStorageConfig,
    S3StorageConfig,
    GCSStorageConfig,
    StorageConfigType,
)
from .crypto import encrypt_credential, decrypt_credential


# Fields that should be encrypted when saving
ENCRYPTED_FIELDS = {
    "azure": ["connection_string", "access_key"],
    "s3": ["access_key_id", "secret_access_key"],
    "gcs": [],  # credentials_path is a file path, not a secret
}


def get_storage_config_path() -> Path:
    """Get the path to the storage configuration file.
    
    Returns:
        Path to ~/.oceanstream/storage.json
    """
    oceanstream_dir = Path.home() / ".oceanstream"
    oceanstream_dir.mkdir(parents=True, exist_ok=True)
    return oceanstream_dir / "storage.json"


def load_storage_configuration() -> StorageConfiguration:
    """Load storage configuration from disk with automatic decryption.
    
    Returns:
        StorageConfiguration object.
        
    Raises:
        FileNotFoundError: If configuration file doesn't exist.
        ValueError: If configuration is invalid.
    """
    config_path = get_storage_config_path()
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    # Load JSON
    with config_path.open("r") as f:
        data = json.load(f)
    
    # Decrypt sensitive fields before deserializing
    for provider_name, provider_dict in data.get("providers", {}).items():
        provider_type = provider_dict.get("provider")
        
        if provider_type in ENCRYPTED_FIELDS:
            for field in ENCRYPTED_FIELDS[provider_type]:
                encrypted_value = provider_dict.get(field)
                if encrypted_value:
                    try:
                        provider_dict[field] = decrypt_credential(encrypted_value)
                    except Exception as e:
                        # If decryption fails, leave as-is and let validation catch it
                        print(f"Warning: Failed to decrypt {field} for {provider_name}: {e}")
    
    # Deserialize to StorageConfiguration
    return StorageConfiguration.from_dict(data)


def save_storage_configuration(config: StorageConfiguration) -> None:
    """Save storage configuration to disk with automatic encryption.
    
    Sensitive fields are encrypted before writing. File permissions are
    set to 600 (owner read/write only).
    
    Args:
        config: StorageConfiguration object to save.
    """
    config_path = get_storage_config_path()
    
    # Serialize to dict
    data = config.to_dict()
    
    # Encrypt sensitive fields
    for provider_name, provider_dict in data.get("providers", {}).items():
        provider_type = provider_dict.get("provider")
        
        if provider_type in ENCRYPTED_FIELDS:
            for field in ENCRYPTED_FIELDS[provider_type]:
                plaintext_value = provider_dict.get(field)
                if plaintext_value:
                    provider_dict[field] = encrypt_credential(plaintext_value)
    
    # Write JSON with nice formatting
    with config_path.open("w") as f:
        json.dump(data, f, indent=2)
    
    # Set restrictive permissions (owner read/write only)
    config_path.chmod(0o600)


def add_azure_storage(
    connection_string: Optional[str] = None,
    account_name: Optional[str] = None,
    access_key: Optional[str] = None,
    container_name: str = "oceanstream-data",
) -> StorageConfiguration:
    """Add or update Azure Blob Storage configuration.
    
    Args:
        connection_string: Azure storage connection string (either this OR account credentials).
        account_name: Storage account name.
        access_key: Storage account access key.
        container_name: Container name for data storage.
        
    Returns:
        Updated StorageConfiguration.
        
    Raises:
        ValueError: If neither connection string nor account credentials provided.
    """
    # Load existing config or create new
    try:
        config = load_storage_configuration()
    except FileNotFoundError:
        config = StorageConfiguration()
    
    # Create Azure config
    azure_config = AzureStorageConfig(
        connection_string=connection_string,
        account_name=account_name,
        access_key=access_key,
        container_name=container_name,
    )
    
    # Validate
    if not azure_config.validate():
        raise ValueError(
            "Azure storage requires either connection_string OR (account_name + access_key)"
        )
    
    # Add to configuration (replaces existing)
    config.add_provider("azure", azure_config)
    config.set_active("azure")
    
    # Save
    save_storage_configuration(config)
    
    return config


def add_local_storage(
    base_path: Optional[Path] = None,
) -> StorageConfiguration:
    """Add or update local filesystem storage configuration.
    
    Args:
        base_path: Base directory for data storage (optional).
        
    Returns:
        Updated StorageConfiguration.
    """
    # Load existing config or create new
    try:
        config = load_storage_configuration()
    except FileNotFoundError:
        config = StorageConfiguration()
    
    # Create local config
    local_config = LocalStorageConfig(base_path=base_path)
    
    # Add to configuration (replaces existing)
    config.add_provider("local", local_config)
    config.set_active("local")
    
    # Save
    save_storage_configuration(config)
    
    return config


def add_s3_storage(
    bucket_name: str = "oceanstream-data",
    region: str = "us-west-2",
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    endpoint_url: Optional[str] = None,
) -> StorageConfiguration:
    """Add or update AWS S3 storage configuration.
    
    Args:
        bucket_name: S3 bucket name.
        region: AWS region.
        access_key_id: AWS access key ID.
        secret_access_key: AWS secret access key.
        endpoint_url: Custom endpoint URL for S3-compatible services.
        
    Returns:
        Updated StorageConfiguration.
    """
    # Load existing config or create new
    try:
        config = load_storage_configuration()
    except FileNotFoundError:
        config = StorageConfiguration()
    
    # Create S3 config
    s3_config = S3StorageConfig(
        bucket_name=bucket_name,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        endpoint_url=endpoint_url,
    )
    
    # Add to configuration (replaces existing)
    config.add_provider("s3", s3_config)
    config.set_active("s3")
    
    # Save
    save_storage_configuration(config)
    
    return config


def add_gcs_storage(
    bucket_name: str = "oceanstream-data",
    project_id: Optional[str] = None,
    credentials_path: Optional[Path] = None,
) -> StorageConfiguration:
    """Add or update Google Cloud Storage configuration.
    
    Args:
        bucket_name: GCS bucket name.
        project_id: GCP project ID.
        credentials_path: Path to service account JSON credentials file.
        
    Returns:
        Updated StorageConfiguration.
    """
    # Load existing config or create new
    try:
        config = load_storage_configuration()
    except FileNotFoundError:
        config = StorageConfiguration()
    
    # Create GCS config
    gcs_config = GCSStorageConfig(
        bucket_name=bucket_name,
        project_id=project_id,
        credentials_path=credentials_path,
    )
    
    # Add to configuration (replaces existing)
    config.add_provider("gcs", gcs_config)
    config.set_active("gcs")
    
    # Save
    save_storage_configuration(config)
    
    return config


def get_active_storage_config() -> tuple[str, StorageConfigType]:
    """Get the active storage provider configuration.
    
    Returns:
        Tuple of (provider_name, config).
        
    Raises:
        FileNotFoundError: If no configuration exists.
        ValueError: If no active provider is set.
    """
    config = load_storage_configuration()
    return config.get_active_config()


def list_storage_providers() -> list[tuple[str, str, bool]]:
    """List all configured storage providers.
    
    Returns:
        List of tuples: (name, provider_type, is_active).
        
    Raises:
        FileNotFoundError: If no configuration exists.
    """
    config = load_storage_configuration()
    return config.list_providers()


def set_active_storage(provider_name: str) -> None:
    """Set the active storage provider.
    
    Args:
        provider_name: Name of provider to set as active.
        
    Raises:
        FileNotFoundError: If no configuration exists.
        ValueError: If provider name not found.
    """
    config = load_storage_configuration()
    config.set_active(provider_name)
    save_storage_configuration(config)


def delete_storage_provider(provider_name: str) -> None:
    """Delete a storage provider configuration.
    
    Args:
        provider_name: Name of provider to delete.
        
    Raises:
        FileNotFoundError: If no configuration exists.
        ValueError: If trying to delete the active provider.
    """
    config = load_storage_configuration()
    config.delete_provider(provider_name)
    save_storage_configuration(config)
