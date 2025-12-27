"""Storage provider implementations.

This module provides a unified interface for uploading data to different
storage backends (Azure, S3, GCS, Local) using credentials from the
storage configuration system.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .config import (
    StorageConfiguration,
    LocalStorageConfig,
    AzureStorageConfig,
    S3StorageConfig,
    GCSStorageConfig,
)
from .manager import load_storage_configuration


class StorageProvider(ABC):
    """Abstract base class for storage providers."""

    @abstractmethod
    def upload_file(self, local_path: Path, remote_path: str) -> str:
        """Upload a file to storage.
        
        Args:
            local_path: Path to local file to upload.
            remote_path: Destination path in storage (relative).
            
        Returns:
            URL or identifier of uploaded file.
        """
        pass

    @abstractmethod
    def upload_directory(self, local_dir: Path, remote_prefix: str) -> list[str]:
        """Upload an entire directory to storage.
        
        Args:
            local_dir: Path to local directory to upload.
            remote_prefix: Destination prefix in storage.
            
        Returns:
            List of URLs/identifiers of uploaded files.
        """
        pass

    @abstractmethod
    def list_files(self, prefix: str = "") -> list[str]:
        """List files in storage.
        
        Args:
            prefix: Optional prefix to filter results.
            
        Returns:
            List of file paths/names.
        """
        pass


class LocalStorageProvider(StorageProvider):
    """Local filesystem storage provider."""

    def __init__(self, config: LocalStorageConfig):
        """Initialize local storage provider.
        
        Args:
            config: Local storage configuration.
        """
        self.base_path = config.base_path or Path("data/output")
        self.base_path.mkdir(parents=True, exist_ok=True)

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        """Copy file to local storage directory.
        
        Args:
            local_path: Path to local file.
            remote_path: Relative destination path.
            
        Returns:
            Absolute path to copied file.
        """
        dest_path = self.base_path / remote_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest_path)
        return str(dest_path.absolute())

    def upload_directory(self, local_dir: Path, remote_prefix: str) -> list[str]:
        """Copy directory to local storage.
        
        Args:
            local_dir: Path to local directory.
            remote_prefix: Destination prefix.
            
        Returns:
            List of copied file paths.
        """
        uploaded = []
        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(local_dir)
                remote_path = f"{remote_prefix}/{relative}"
                result = self.upload_file(file_path, remote_path)
                uploaded.append(result)
        return uploaded

    def list_files(self, prefix: str = "") -> list[str]:
        """List files in local storage.
        
        Args:
            prefix: Optional prefix to filter results.
            
        Returns:
            List of file paths relative to base_path.
        """
        search_path = self.base_path / prefix if prefix else self.base_path
        if not search_path.exists():
            return []
        
        files = []
        for file_path in search_path.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(self.base_path)
                files.append(str(relative))
        return sorted(files)


class AzureStorageProvider(StorageProvider):
    """Azure Blob Storage provider."""

    def __init__(self, config: AzureStorageConfig):
        """Initialize Azure storage provider.
        
        Args:
            config: Azure storage configuration.
            
        Raises:
            ImportError: If azure-storage-blob is not installed.
        """
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as e:
            raise ImportError(
                "azure-storage-blob is required for Azure storage. "
                "Install with: pip install azure-storage-blob"
            ) from e

        self.container_name = config.container_name

        # Connect using either connection string or account credentials
        if config.connection_string:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                config.connection_string
            )
        elif config.account_name and config.access_key:
            account_url = f"https://{config.account_name}.blob.core.windows.net"
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=config.access_key,
            )
        else:
            raise ValueError(
                "Azure storage requires either connection_string OR (account_name + access_key)"
            )

        # Get container client
        self.container_client = self.blob_service_client.get_container_client(
            self.container_name
        )

        # Create container if it doesn't exist
        try:
            self.container_client.create_container()
        except Exception:
            # Container might already exist, which is fine
            pass

    def upload_file(self, local_path: Path, remote_path: str) -> str:
        """Upload file to Azure Blob Storage.
        
        Args:
            local_path: Path to local file.
            remote_path: Blob name (relative path).
            
        Returns:
            URL of uploaded blob.
        """
        blob_client = self.blob_service_client.get_blob_client(
            container=self.container_name,
            blob=remote_path,
        )

        with open(local_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)

        return blob_client.url

    def upload_directory(self, local_dir: Path, remote_prefix: str) -> list[str]:
        """Upload directory to Azure Blob Storage.
        
        Args:
            local_dir: Path to local directory.
            remote_prefix: Blob prefix for uploaded files.
            
        Returns:
            List of uploaded blob URLs.
        """
        uploaded = []
        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(local_dir)
                blob_name = f"{remote_prefix}/{relative}".replace("\\", "/")
                result = self.upload_file(file_path, blob_name)
                uploaded.append(result)
        return uploaded

    def list_files(self, prefix: str = "") -> list[str]:
        """List blobs in container.
        
        Args:
            prefix: Optional prefix to filter results.
            
        Returns:
            List of blob names.
        """
        blobs = self.container_client.list_blobs(name_starts_with=prefix)
        return sorted([blob.name for blob in blobs])


def get_storage_provider(
    provider_name: Optional[str] = None,
    config: Optional[StorageConfiguration] = None,
) -> StorageProvider:
    """Get a storage provider instance.
    
    Args:
        provider_name: Name of provider to use (e.g., 'azure', 'local').
                      If None, uses active provider from config.
        config: StorageConfiguration to use. If None, loads from disk.
        
    Returns:
        StorageProvider instance.
        
    Raises:
        ValueError: If provider not found or not configured.
        FileNotFoundError: If no configuration file exists.
    """
    # Load config if not provided
    if config is None:
        config = load_storage_configuration()

    # Determine which provider to use
    if provider_name is None:
        provider_name = config.active_provider
        if not provider_name:
            raise ValueError("No active provider configured")

    # Get provider config
    provider_config = config.providers.get(provider_name)
    if not provider_config:
        raise ValueError(f"Provider '{provider_name}' not configured")

    # Create appropriate provider instance
    if isinstance(provider_config, LocalStorageConfig):
        return LocalStorageProvider(provider_config)
    elif isinstance(provider_config, AzureStorageConfig):
        return AzureStorageProvider(provider_config)
    else:
        raise ValueError(f"Unknown provider type: {type(provider_config)}")
