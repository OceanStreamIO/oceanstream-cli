"""Tests for the storage filesystem module.

Tests for PyArrow filesystem creation and storage path resolution.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from oceanstream.storage.filesystem import (
    parse_storage_uri,
    resolve_output_path,
    create_azure_filesystem,
    create_s3_filesystem,
    create_filesystem_from_config,
    StoragePath,
)
from oceanstream.storage.config import (
    LocalStorageConfig,
    AzureStorageConfig,
    S3StorageConfig,
    StorageConfiguration,
)


class TestParseStorageUri:
    """Tests for URI parsing."""
    
    def test_azure_uri(self):
        """Test Azure URI parsing."""
        scheme, bucket, path = parse_storage_uri("az://mycontainer/path/to/data")
        assert scheme == "azure"
        assert bucket == "mycontainer"
        assert path == "path/to/data"
    
    def test_azure_abfs_uri(self):
        """Test Azure abfs:// URI parsing."""
        scheme, bucket, path = parse_storage_uri("abfs://container/folder")
        assert scheme == "azure"
        assert bucket == "container"
        assert path == "folder"
    
    def test_s3_uri(self):
        """Test S3 URI parsing."""
        scheme, bucket, path = parse_storage_uri("s3://mybucket/prefix/data")
        assert scheme == "s3"
        assert bucket == "mybucket"
        assert path == "prefix/data"
    
    def test_gcs_uri(self):
        """Test GCS URI parsing."""
        scheme, bucket, path = parse_storage_uri("gs://gcsbucket/folder")
        assert scheme == "gcs"
        assert bucket == "gcsbucket"
        assert path == "folder"
    
    def test_local_absolute_path(self):
        """Test local absolute path."""
        scheme, bucket, path = parse_storage_uri("/absolute/path/to/data")
        assert scheme == "local"
        assert bucket == ""
        assert path == "/absolute/path/to/data"
    
    def test_local_relative_path(self):
        """Test local relative path."""
        scheme, bucket, path = parse_storage_uri("./relative/path")
        assert scheme == "local"
        assert bucket == ""
        assert path == "./relative/path"
    
    def test_file_scheme(self):
        """Test file:// URI."""
        scheme, bucket, path = parse_storage_uri("file:///home/user/data")
        assert scheme == "local"
        assert bucket == ""
        assert path == "/home/user/data"
    
    def test_container_only_uri(self):
        """Test URI with container only (no path)."""
        scheme, bucket, path = parse_storage_uri("az://mycontainer")
        assert scheme == "azure"
        assert bucket == "mycontainer"
        assert path == ""


class TestCreateAzureFilesystem:
    """Tests for Azure filesystem creation."""
    
    def test_create_with_account_key(self):
        """Test creating Azure filesystem with account name and key."""
        config = AzureStorageConfig(
            account_name="testaccount",
            access_key="testkey123",
            container_name="testcontainer",
        )
        
        with patch("pyarrow.fs.AzureFileSystem") as mock_fs:
            fs = create_azure_filesystem(config)
            mock_fs.assert_called_once_with(
                account_name="testaccount",
                account_key="testkey123",
            )
    
    def test_create_with_connection_string(self):
        """Test creating Azure filesystem with connection string."""
        config = AzureStorageConfig(
            connection_string="AccountName=testaccount;AccountKey=testkey123",
            container_name="testcontainer",
        )
        
        with patch("pyarrow.fs.AzureFileSystem") as mock_fs:
            fs = create_azure_filesystem(config)
            mock_fs.assert_called_once_with(
                account_name="testaccount",
                account_key="testkey123",
            )
    
    def test_create_without_credentials_raises(self):
        """Test that missing credentials raises ValueError."""
        config = AzureStorageConfig(
            container_name="testcontainer",
        )
        
        with pytest.raises(ValueError, match="account_name or connection_string"):
            create_azure_filesystem(config)


class TestCreateS3Filesystem:
    """Tests for S3 filesystem creation."""
    
    def test_create_with_credentials(self):
        """Test creating S3 filesystem with credentials."""
        config = S3StorageConfig(
            bucket_name="testbucket",
            region="us-west-2",
            access_key_id="AKIATEST",
            secret_access_key="secretkey",
        )
        
        with patch("pyarrow.fs.S3FileSystem") as mock_fs:
            fs = create_s3_filesystem(config)
            mock_fs.assert_called_once_with(
                region="us-west-2",
                access_key="AKIATEST",
                secret_key="secretkey",
            )
    
    def test_create_with_endpoint_url(self):
        """Test creating S3 filesystem with custom endpoint (MinIO, etc.)."""
        config = S3StorageConfig(
            bucket_name="testbucket",
            region="us-east-1",
            endpoint_url="http://localhost:9000",
        )
        
        with patch("pyarrow.fs.S3FileSystem") as mock_fs:
            fs = create_s3_filesystem(config)
            mock_fs.assert_called_once_with(
                region="us-east-1",
                endpoint_override="http://localhost:9000",
            )


class TestCreateFilesystemFromConfig:
    """Tests for filesystem factory."""
    
    def test_local_config(self, tmp_path):
        """Test creating filesystem from local config."""
        config = LocalStorageConfig(base_path=tmp_path)
        
        fs, base_path = create_filesystem_from_config(config)
        
        assert base_path == str(tmp_path)
        # Should be a LocalFileSystem (checking type name since mocking is complex)
        assert "Local" in type(fs).__name__
    
    def test_azure_config(self):
        """Test creating filesystem from Azure config."""
        config = AzureStorageConfig(
            account_name="testaccount",
            access_key="testkey",
            container_name="mycontainer",
        )
        
        with patch("pyarrow.fs.AzureFileSystem") as mock_fs:
            mock_fs.return_value = MagicMock()
            fs, base_path = create_filesystem_from_config(config)
            
            assert base_path == "mycontainer"
            mock_fs.assert_called_once()
    
    def test_s3_config(self):
        """Test creating filesystem from S3 config."""
        config = S3StorageConfig(
            bucket_name="mybucket",
            region="eu-west-1",
        )
        
        with patch("pyarrow.fs.S3FileSystem") as mock_fs:
            mock_fs.return_value = MagicMock()
            fs, base_path = create_filesystem_from_config(config)
            
            assert base_path == "mybucket"
            mock_fs.assert_called_once()


class TestResolveOutputPath:
    """Tests for output path resolution."""
    
    def test_local_path_no_config(self, tmp_path):
        """Test resolving local path without any cloud config."""
        output_dir = tmp_path / "output"
        
        result = resolve_output_path(output_dir, use_active_storage=False)
        
        assert result.is_cloud is False
        assert result.provider == "local"
        assert str(output_dir.resolve()) in result.path
    
    def test_cloud_uri_azure(self):
        """Test resolving Azure cloud URI."""
        # Create a mock storage config with Azure credentials
        azure_config = AzureStorageConfig(
            account_name="testaccount",
            access_key="testkey",
            container_name="default",
        )
        storage_config = StorageConfiguration()
        storage_config.add_provider("azure", azure_config)
        
        with patch("pyarrow.fs.AzureFileSystem") as mock_fs:
            mock_fs.return_value = MagicMock()
            
            result = resolve_output_path(
                "az://mycontainer/path/to/output",
                storage_config=storage_config,
            )
            
            assert result.is_cloud is True
            assert result.provider == "azure"
            assert "mycontainer" in result.path
            assert "path/to/output" in result.path
    
    def test_local_path_with_active_cloud_storage(self, tmp_path):
        """Test local path redirects to cloud when active storage is configured."""
        azure_config = AzureStorageConfig(
            account_name="testaccount",
            access_key="testkey",
            container_name="oceandata",
        )
        storage_config = StorageConfiguration()
        storage_config.add_provider("azure", azure_config)
        storage_config.set_active("azure")
        
        with patch("pyarrow.fs.AzureFileSystem") as mock_fs:
            mock_fs.return_value = MagicMock()
            
            result = resolve_output_path(
                "out/geoparquet",
                storage_config=storage_config,
                use_active_storage=True,
            )
            
            assert result.is_cloud is True
            assert result.provider == "azure"
            assert "oceandata" in result.path
            assert "out/geoparquet" in result.path
    
    def test_local_path_ignores_cloud_when_disabled(self, tmp_path):
        """Test local path stays local when use_active_storage is False."""
        azure_config = AzureStorageConfig(
            account_name="testaccount",
            access_key="testkey",
            container_name="oceandata",
        )
        storage_config = StorageConfiguration()
        storage_config.add_provider("azure", azure_config)
        storage_config.set_active("azure")
        
        output_dir = tmp_path / "output"
        result = resolve_output_path(
            output_dir,
            storage_config=storage_config,
            use_active_storage=False,
        )
        
        assert result.is_cloud is False
        assert result.provider == "local"


class TestStoragePath:
    """Tests for StoragePath dataclass."""
    
    def test_storage_path_attributes(self):
        """Test StoragePath holds correct attributes."""
        import pyarrow.fs as pafs
        
        fs = pafs.LocalFileSystem()
        sp = StoragePath(
            filesystem=fs,
            path="/test/path",
            is_cloud=False,
            provider="local",
        )
        
        assert sp.filesystem == fs
        assert sp.path == "/test/path"
        assert sp.is_cloud is False
        assert sp.provider == "local"
