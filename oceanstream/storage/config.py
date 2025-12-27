"""Storage configuration data models.

This module defines the data structures for storage provider configurations,
supporting local filesystem, Azure Blob Storage, AWS S3, and Google Cloud Storage.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union


class StorageProvider(str, Enum):
    """Supported storage providers."""
    LOCAL = "local"
    AZURE = "azure"
    S3 = "s3"
    GCS = "gcs"


@dataclass
class LocalStorageConfig:
    """Configuration for local filesystem storage."""
    provider: str = "local"
    base_path: Optional[Path] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "provider": self.provider,
            "base_path": str(self.base_path) if self.base_path else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "LocalStorageConfig":
        """Create from dictionary."""
        return cls(
            provider=data.get("provider", "local"),
            base_path=Path(data["base_path"]) if data.get("base_path") else None,
        )


@dataclass
class AzureStorageConfig:
    """Configuration for Azure Blob Storage.
    
    Supports either connection string OR account name + access key.
    """
    provider: str = "azure"
    connection_string: Optional[str] = None
    account_name: Optional[str] = None
    access_key: Optional[str] = None
    container_name: str = "oceanstream-data"
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "provider": self.provider,
            "connection_string": self.connection_string,
            "account_name": self.account_name,
            "access_key": self.access_key,
            "container_name": self.container_name,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AzureStorageConfig":
        """Create from dictionary."""
        return cls(
            provider=data.get("provider", "azure"),
            connection_string=data.get("connection_string"),
            account_name=data.get("account_name"),
            access_key=data.get("access_key"),
            container_name=data.get("container_name", "oceanstream-data"),
        )
    
    def validate(self) -> bool:
        """Validate that either connection string or account credentials are provided."""
        if self.connection_string:
            return True
        if self.account_name and self.access_key:
            return True
        return False


@dataclass
class S3StorageConfig:
    """Configuration for AWS S3 storage."""
    provider: str = "s3"
    bucket_name: str = "oceanstream-data"
    region: str = "us-west-2"
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    endpoint_url: Optional[str] = None  # For S3-compatible services
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "provider": self.provider,
            "bucket_name": self.bucket_name,
            "region": self.region,
            "access_key_id": self.access_key_id,
            "secret_access_key": self.secret_access_key,
            "endpoint_url": self.endpoint_url,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "S3StorageConfig":
        """Create from dictionary."""
        return cls(
            provider=data.get("provider", "s3"),
            bucket_name=data.get("bucket_name", "oceanstream-data"),
            region=data.get("region", "us-west-2"),
            access_key_id=data.get("access_key_id"),
            secret_access_key=data.get("secret_access_key"),
            endpoint_url=data.get("endpoint_url"),
        )


@dataclass
class GCSStorageConfig:
    """Configuration for Google Cloud Storage."""
    provider: str = "gcs"
    bucket_name: str = "oceanstream-data"
    project_id: Optional[str] = None
    credentials_path: Optional[Path] = None  # Path to service account JSON
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "provider": self.provider,
            "bucket_name": self.bucket_name,
            "project_id": self.project_id,
            "credentials_path": str(self.credentials_path) if self.credentials_path else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "GCSStorageConfig":
        """Create from dictionary."""
        return cls(
            provider=data.get("provider", "gcs"),
            bucket_name=data.get("bucket_name", "oceanstream-data"),
            project_id=data.get("project_id"),
            credentials_path=Path(data["credentials_path"]) if data.get("credentials_path") else None,
        )


StorageConfigType = Union[LocalStorageConfig, AzureStorageConfig, S3StorageConfig, GCSStorageConfig]


@dataclass
class StorageConfiguration:
    """Root configuration object managing storage providers.
    
    Supports multiple provider configurations with one active at a time.
    """
    providers: dict[str, StorageConfigType] = field(default_factory=dict)
    active_provider: Optional[str] = None
    version: str = "1.0"
    
    def add_provider(self, name: str, config: StorageConfigType) -> None:
        """Add or update a storage provider configuration."""
        self.providers[name] = config
        
        # Set as active if it's the first one
        if self.active_provider is None:
            self.active_provider = name
    
    def get_provider(self, name: str) -> Optional[StorageConfigType]:
        """Get a storage provider configuration by name."""
        return self.providers.get(name)
    
    def get_active_config(self) -> tuple[str, StorageConfigType]:
        """Get the active storage provider configuration.
        
        Returns:
            Tuple of (provider_name, config).
            
        Raises:
            ValueError: If no active provider is set.
        """
        if self.active_provider is None:
            raise ValueError("No active storage provider configured")
        
        config = self.providers.get(self.active_provider)
        if config is None:
            raise ValueError(f"Active provider '{self.active_provider}' not found in configuration")
        
        return self.active_provider, config
    
    def set_active(self, name: str) -> None:
        """Set the active storage provider.
        
        Args:
            name: Provider name to set as active.
            
        Raises:
            ValueError: If provider name not found.
        """
        if name not in self.providers:
            raise ValueError(f"Provider '{name}' not found")
        
        self.active_provider = name
    
    def delete_provider(self, name: str) -> None:
        """Delete a storage provider configuration.
        
        Args:
            name: Provider name to delete.
            
        Raises:
            ValueError: If trying to delete the active provider.
        """
        if name == self.active_provider:
            raise ValueError("Cannot delete active provider")
        
        if name in self.providers:
            del self.providers[name]
    
    def list_providers(self) -> list[tuple[str, str, bool]]:
        """List all configured providers.
        
        Returns:
            List of tuples: (name, provider_type, is_active).
        """
        return [
            (name, config.provider, name == self.active_provider)
            for name, config in self.providers.items()
        ]
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "active_provider": self.active_provider,
            "providers": {
                name: config.to_dict()
                for name, config in self.providers.items()
            },
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "StorageConfiguration":
        """Create from dictionary."""
        config = cls(
            version=data.get("version", "1.0"),
            active_provider=data.get("active_provider"),
        )
        
        # Deserialize each provider
        providers_data = data.get("providers", {})
        for name, provider_dict in providers_data.items():
            provider_type = provider_dict.get("provider")
            
            if provider_type == "local":
                provider_config = LocalStorageConfig.from_dict(provider_dict)
            elif provider_type == "azure":
                provider_config = AzureStorageConfig.from_dict(provider_dict)
            elif provider_type == "s3":
                provider_config = S3StorageConfig.from_dict(provider_dict)
            elif provider_type == "gcs":
                provider_config = GCSStorageConfig.from_dict(provider_dict)
            else:
                raise ValueError(f"Unknown provider type: {provider_type}")
            
            config.providers[name] = provider_config
        
        return config
