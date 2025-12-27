"""PyArrow filesystem factory for cloud storage.

This module provides a unified interface for creating PyArrow filesystems
from storage configurations, enabling direct cloud writes for GeoParquet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union
from urllib.parse import urlparse

import pyarrow.fs as pafs

from .config import (
    StorageConfiguration,
    LocalStorageConfig,
    AzureStorageConfig,
    S3StorageConfig,
    GCSStorageConfig,
    StorageConfigType,
)
from .manager import load_storage_configuration


@dataclass
class StoragePath:
    """Parsed storage path with filesystem and path components.
    
    Attributes:
        filesystem: PyArrow filesystem instance
        path: Path within the filesystem (container/bucket + prefix)
        is_cloud: Whether this is a cloud storage path
        provider: Storage provider name (local, azure, s3, gcs)
    """
    filesystem: pafs.FileSystem
    path: str
    is_cloud: bool
    provider: str


def create_azure_filesystem(config: AzureStorageConfig) -> pafs.AzureFileSystem:
    """Create PyArrow AzureFileSystem from config.
    
    Args:
        config: Azure storage configuration.
        
    Returns:
        Configured AzureFileSystem instance.
        
    Raises:
        ValueError: If required credentials are missing.
    """
    if not config.account_name and not config.connection_string:
        raise ValueError("Azure storage requires account_name or connection_string")
    
    # Extract account name from connection string if needed
    account_name = config.account_name
    account_key = config.access_key
    
    if config.connection_string and not account_name:
        # Parse connection string to extract account name and key
        parts = dict(part.split("=", 1) for part in config.connection_string.split(";") if "=" in part)
        account_name = parts.get("AccountName")
        account_key = account_key or parts.get("AccountKey")
    
    if not account_name:
        raise ValueError("Could not determine Azure account name from config")
    
    return pafs.AzureFileSystem(
        account_name=account_name,
        account_key=account_key,
    )


def create_s3_filesystem(config: S3StorageConfig) -> pafs.S3FileSystem:
    """Create PyArrow S3FileSystem from config.
    
    Args:
        config: S3 storage configuration.
        
    Returns:
        Configured S3FileSystem instance.
    """
    kwargs = {
        "region": config.region,
    }
    
    if config.access_key_id and config.secret_access_key:
        kwargs["access_key"] = config.access_key_id
        kwargs["secret_key"] = config.secret_access_key
    
    if config.endpoint_url:
        kwargs["endpoint_override"] = config.endpoint_url
    
    return pafs.S3FileSystem(**kwargs)


def create_gcs_filesystem(config: GCSStorageConfig) -> pafs.GcsFileSystem:
    """Create PyArrow GcsFileSystem from config.
    
    Args:
        config: GCS storage configuration.
        
    Returns:
        Configured GcsFileSystem instance.
    """
    kwargs = {}
    
    if config.project_id:
        kwargs["project_id"] = config.project_id
    
    # Note: GcsFileSystem uses Application Default Credentials by default
    # For explicit credentials, the credentials_path would need special handling
    
    return pafs.GcsFileSystem(**kwargs)


def create_filesystem_from_config(
    config: StorageConfigType,
) -> Tuple[pafs.FileSystem, str]:
    """Create a PyArrow filesystem from storage configuration.
    
    Args:
        config: Storage provider configuration.
        
    Returns:
        Tuple of (filesystem, base_path) where base_path is the container/bucket name.
        
    Raises:
        ValueError: If provider type is not supported.
    """
    if isinstance(config, LocalStorageConfig):
        base_path = str(config.base_path) if config.base_path else "."
        return pafs.LocalFileSystem(), base_path
    
    elif isinstance(config, AzureStorageConfig):
        fs = create_azure_filesystem(config)
        return fs, config.container_name
    
    elif isinstance(config, S3StorageConfig):
        fs = create_s3_filesystem(config)
        return fs, config.bucket_name
    
    elif isinstance(config, GCSStorageConfig):
        fs = create_gcs_filesystem(config)
        return fs, config.bucket_name
    
    else:
        raise ValueError(f"Unsupported storage config type: {type(config)}")


def parse_storage_uri(uri: str) -> Tuple[str, str, str]:
    """Parse a storage URI into (scheme, bucket/container, path).
    
    Supports:
        - az://container/path or abfs://container/path (Azure)
        - s3://bucket/path (S3)
        - gs://bucket/path (GCS)
        - file:///path or /path (local)
        
    Args:
        uri: Storage URI string.
        
    Returns:
        Tuple of (scheme, bucket/container, path).
    """
    parsed = urlparse(uri)
    
    if parsed.scheme in ("az", "abfs", "azure"):
        # Azure: az://container/path
        container = parsed.netloc
        path = parsed.path.lstrip("/")
        return "azure", container, path
    
    elif parsed.scheme == "s3":
        # S3: s3://bucket/path
        bucket = parsed.netloc
        path = parsed.path.lstrip("/")
        return "s3", bucket, path
    
    elif parsed.scheme in ("gs", "gcs"):
        # GCS: gs://bucket/path
        bucket = parsed.netloc
        path = parsed.path.lstrip("/")
        return "gcs", bucket, path
    
    elif parsed.scheme == "file" or not parsed.scheme:
        # Local: file:///path or /path
        path = parsed.path if parsed.path else uri
        return "local", "", path
    
    else:
        # Assume local path
        return "local", "", uri


def resolve_output_path(
    output_dir: Union[str, Path],
    storage_config: Optional[StorageConfiguration] = None,
    use_active_storage: bool = True,
) -> StoragePath:
    """Resolve an output directory to a filesystem and path.
    
    This function determines the appropriate filesystem based on:
    1. If output_dir is a cloud URI (az://, s3://, gs://), use that directly
    2. If use_active_storage is True and storage config has an active cloud provider,
       use that provider with output_dir as the path prefix
    3. Otherwise, use local filesystem
    
    Args:
        output_dir: Output directory path or URI.
        storage_config: Optional storage configuration. If None, will try to load from disk.
        use_active_storage: If True, use active storage provider when output_dir is local path.
        
    Returns:
        StoragePath with filesystem, path, and metadata.
    """
    output_str = str(output_dir)
    
    # Check if output_dir is already a cloud URI
    scheme, bucket, path = parse_storage_uri(output_str)
    
    if scheme != "local" and bucket:
        # It's a cloud URI - create filesystem from URI
        if scheme == "azure":
            # Try credentials from config first, then environment variables
            import os
            azure_config = None
            if storage_config is None:
                try:
                    storage_config = load_storage_configuration()
                except FileNotFoundError:
                    pass
            
            if storage_config:
                azure_config = storage_config.providers.get("azure")
            
            # Get credentials from config if available and valid
            connection_string = None
            account_name = None
            account_key = None
            
            if azure_config and isinstance(azure_config, AzureStorageConfig):
                # Only use config values if they look valid (not encrypted garbage)
                if azure_config.connection_string and "AccountName=" in azure_config.connection_string:
                    connection_string = azure_config.connection_string
                if azure_config.account_name:
                    account_name = azure_config.account_name
                if azure_config.access_key:
                    account_key = azure_config.access_key
            
            # Fall back to environment variables if config not available
            if not connection_string and not account_name:
                connection_string = os.environ.get("AZURE_CONNECTION_STRING")
                account_name = os.environ.get("AZURE_STORAGE_ACCOUNT")
                account_key = os.environ.get("AZURE_STORAGE_KEY")
            
            if not connection_string and not account_name:
                raise ValueError(
                    f"Cloud URI '{output_str}' requires Azure credentials. "
                    "Set AZURE_CONNECTION_STRING env var or run 'oceanstream configure'."
                )
            
            # Create config from available credentials
            azure_config_copy = AzureStorageConfig(
                provider="azure",
                connection_string=connection_string,
                account_name=account_name,
                access_key=account_key,
                container_name=bucket,
            )
            fs = create_azure_filesystem(azure_config_copy)
            full_path = f"{bucket}/{path}" if path else bucket
            return StoragePath(
                filesystem=fs,
                path=full_path,
                is_cloud=True,
                provider="azure",
            )
        
        elif scheme == "s3":
            if storage_config is None:
                try:
                    storage_config = load_storage_configuration()
                except FileNotFoundError:
                    # S3 can use default credentials from environment
                    pass
            
            s3_config = None
            if storage_config:
                s3_config = storage_config.providers.get("s3")
            
            if s3_config and isinstance(s3_config, S3StorageConfig):
                fs = create_s3_filesystem(s3_config)
            else:
                # Use default credentials
                fs = pafs.S3FileSystem()
            
            full_path = f"{bucket}/{path}" if path else bucket
            return StoragePath(
                filesystem=fs,
                path=full_path,
                is_cloud=True,
                provider="s3",
            )
        
        elif scheme == "gcs":
            if storage_config is None:
                try:
                    storage_config = load_storage_configuration()
                except FileNotFoundError:
                    pass
            
            gcs_config = None
            if storage_config:
                gcs_config = storage_config.providers.get("gcs")
            
            if gcs_config and isinstance(gcs_config, GCSStorageConfig):
                fs = create_gcs_filesystem(gcs_config)
            else:
                fs = pafs.GcsFileSystem()
            
            full_path = f"{bucket}/{path}" if path else bucket
            return StoragePath(
                filesystem=fs,
                path=full_path,
                is_cloud=True,
                provider="gcs",
            )
    
    # Local path - but check if we should use active cloud storage
    if use_active_storage:
        if storage_config is None:
            try:
                storage_config = load_storage_configuration()
            except FileNotFoundError:
                storage_config = None
        
        if storage_config and storage_config.active_provider:
            active_config = storage_config.providers.get(storage_config.active_provider)
            
            if active_config and not isinstance(active_config, LocalStorageConfig):
                # Use cloud storage with output_dir as path prefix
                fs, base_path = create_filesystem_from_config(active_config)
                
                # Combine base path (container/bucket) with output_dir as prefix
                relative_path = str(output_dir).lstrip("/").lstrip("./")
                full_path = f"{base_path}/{relative_path}" if relative_path else base_path
                
                return StoragePath(
                    filesystem=fs,
                    path=full_path,
                    is_cloud=True,
                    provider=storage_config.active_provider,
                )
    
    # Default to local filesystem
    local_path = Path(output_dir).resolve()
    return StoragePath(
        filesystem=pafs.LocalFileSystem(),
        path=str(local_path),
        is_cloud=False,
        provider="local",
    )
