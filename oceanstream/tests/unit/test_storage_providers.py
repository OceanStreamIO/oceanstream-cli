"""Tests for storage provider implementations."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from oceanstream.storage.providers import (
    LocalStorageProvider,
    AzureStorageProvider,
    get_storage_provider,
)
from oceanstream.storage.config import (
    StorageConfiguration,
    LocalStorageConfig,
    AzureStorageConfig,
)


class TestLocalStorageProvider:
    """Tests for LocalStorageProvider."""

    def test_upload_file(self, tmp_path):
        """Test uploading a file to local storage."""
        # Setup
        base_path = tmp_path / "storage"
        config = LocalStorageConfig(base_path=base_path)
        provider = LocalStorageProvider(config)

        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        # Upload
        result = provider.upload_file(test_file, "subdir/test.txt")

        # Verify
        expected_path = base_path / "subdir" / "test.txt"
        assert expected_path.exists()
        assert expected_path.read_text() == "test content"
        assert result == str(expected_path.absolute())

    def test_upload_directory(self, tmp_path):
        """Test uploading a directory to local storage."""
        # Setup
        base_path = tmp_path / "storage"
        config = LocalStorageConfig(base_path=base_path)
        provider = LocalStorageProvider(config)

        # Create test directory
        test_dir = tmp_path / "data"
        test_dir.mkdir()
        (test_dir / "file1.txt").write_text("content1")
        (test_dir / "subdir").mkdir()
        (test_dir / "subdir" / "file2.txt").write_text("content2")

        # Upload
        results = provider.upload_directory(test_dir, "campaign")

        # Verify
        assert len(results) == 2
        assert (base_path / "campaign" / "file1.txt").exists()
        assert (base_path / "campaign" / "subdir" / "file2.txt").exists()

    def test_list_files(self, tmp_path):
        """Test listing files in local storage."""
        # Setup
        base_path = tmp_path / "storage"
        base_path.mkdir()
        (base_path / "file1.txt").write_text("content1")
        (base_path / "subdir").mkdir()
        (base_path / "subdir" / "file2.txt").write_text("content2")

        config = LocalStorageConfig(base_path=base_path)
        provider = LocalStorageProvider(config)

        # List all files
        files = provider.list_files()
        assert len(files) == 2
        assert "file1.txt" in files
        assert str(Path("subdir") / "file2.txt") in files


class TestAzureStorageProvider:
    """Tests for AzureStorageProvider."""

    def test_init_with_connection_string(self):
        """Test initialization with connection string."""
        config = AzureStorageConfig(
            connection_string="DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key123",
            container_name="test-container",
        )

        with patch("azure.storage.blob.BlobServiceClient") as mock_client:
            provider = AzureStorageProvider(config)

            mock_client.from_connection_string.assert_called_once()
            assert provider.container_name == "test-container"

    def test_upload_file(self, tmp_path):
        """Test uploading a file to Azure."""
        config = AzureStorageConfig(
            connection_string="DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key123",
            container_name="test-container",
        )

        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        with patch("azure.storage.blob.BlobServiceClient") as mock_client:
            # Setup mocks
            mock_blob_client = MagicMock()
            mock_blob_client.url = "https://test.blob.core.windows.net/test-container/test.txt"
            mock_client.from_connection_string.return_value.get_blob_client.return_value = mock_blob_client

            provider = AzureStorageProvider(config)
            result = provider.upload_file(test_file, "test.txt")

            # Verify
            assert result == mock_blob_client.url
            mock_blob_client.upload_blob.assert_called_once()


class TestGetStorageProvider:
    """Tests for get_storage_provider factory function."""

    def test_get_local_provider(self, tmp_path):
        """Test getting a local storage provider."""
        config = StorageConfiguration()
        config.add_provider("local", LocalStorageConfig(base_path=tmp_path))
        config.set_active("local")

        provider = get_storage_provider(config=config)
        assert isinstance(provider, LocalStorageProvider)

    def test_no_active_provider(self):
        """Test error when no active provider is set."""
        config = StorageConfiguration()
        # Don't add any providers - active_provider will be None

        with pytest.raises(ValueError, match="No active provider configured"):
            get_storage_provider(config=config)
