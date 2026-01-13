"""Tests for echodata cloud storage module."""

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


class TestGetAzureCredentials:
    """Tests for get_azure_credentials function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn-str"}, clear=True)
    def test_get_credentials_from_env(self):
        """Test getting credentials from environment variables."""
        from oceanstream.echodata.storage import get_azure_credentials

        conn_str, container = get_azure_credentials()
        assert conn_str == "test-conn-str"
        assert container == "oceanstream-data"

    @patch.dict(
        "os.environ",
        {"AZURE_CONNECTION_STRING": "test-conn", "AZURE_CONTAINER_NAME": "my-container"},
        clear=True,
    )
    def test_get_credentials_with_custom_container(self):
        """Test getting credentials with custom container from environment."""
        from oceanstream.echodata.storage import get_azure_credentials

        conn_str, container = get_azure_credentials()
        assert conn_str == "test-conn"
        assert container == "my-container"

    @patch.dict("os.environ", {}, clear=True)
    @patch("oceanstream.storage.manager.load_storage_configuration")
    def test_get_credentials_no_env_raises(self, mock_load_config):
        """Test that missing credentials raises ValueError."""
        from oceanstream.echodata.storage import get_azure_credentials

        # Mock the storage configuration to return empty providers
        mock_config = MagicMock()
        mock_config.providers = {}
        mock_load_config.return_value = mock_config

        with pytest.raises(ValueError, match="Azure credentials not found"):
            get_azure_credentials()


class TestIsAzureConfigured:
    """Tests for is_azure_configured function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    def test_azure_configured_returns_true(self):
        """Test that is_azure_configured returns True when credentials exist."""
        from oceanstream.echodata.storage import is_azure_configured

        assert is_azure_configured() is True

    @patch.dict("os.environ", {}, clear=True)
    @patch("oceanstream.storage.manager.load_storage_configuration")
    def test_azure_not_configured_returns_false(self, mock_load_config):
        """Test that is_azure_configured returns False when no credentials."""
        from oceanstream.echodata.storage import is_azure_configured

        # Mock the storage configuration to return empty providers
        mock_config = MagicMock()
        mock_config.providers = {}
        mock_load_config.return_value = mock_config

        assert is_azure_configured() is False


class TestBuildEchodataPath:
    """Tests for build_echodata_path function."""

    def test_build_path_converted(self):
        """Test building path for converted stage."""
        from oceanstream.echodata.storage import build_echodata_path

        path = build_echodata_path("campaign_1", "file_001", stage="converted")
        assert path == "echodata/campaign_1/converted/file_001.zarr"

    def test_build_path_calibrated(self):
        """Test building path for calibrated stage."""
        from oceanstream.echodata.storage import build_echodata_path

        path = build_echodata_path("TPOS_2023", "sd1030_20230101", stage="calibrated")
        assert path == "echodata/TPOS_2023/calibrated/sd1030_20230101.zarr"

    def test_build_path_products(self):
        """Test building path for products stage."""
        from oceanstream.echodata.storage import build_echodata_path

        path = build_echodata_path("test_campaign", "mvbs_output", stage="products")
        assert path == "echodata/test_campaign/products/mvbs_output.zarr"

    def test_build_path_strips_extension(self):
        """Test that file extensions are stripped from filename."""
        from oceanstream.echodata.storage import build_echodata_path

        path = build_echodata_path("campaign", "file.raw", stage="converted")
        assert path == "echodata/campaign/converted/file.zarr"


class TestGetZarrStoreUri:
    """Tests for get_zarr_store_uri function."""

    @patch.dict(
        "os.environ",
        {"AZURE_CONNECTION_STRING": "conn", "AZURE_CONTAINER_NAME": "container"},
        clear=True,
    )
    def test_get_zarr_uri(self):
        """Test getting zarr store URI."""
        from oceanstream.echodata.storage import get_zarr_store_uri

        uri = get_zarr_store_uri("echodata/test/file.zarr")
        assert uri == "abfs://container/echodata/test/file.zarr"

    @patch.dict(
        "os.environ",
        {"AZURE_CONNECTION_STRING": "conn", "AZURE_CONTAINER_NAME": "default"},
        clear=True,
    )
    def test_get_zarr_uri_custom_container(self):
        """Test getting zarr URI with custom container."""
        from oceanstream.echodata.storage import get_zarr_store_uri

        uri = get_zarr_store_uri("path/to/file.zarr", container="custom-container")
        assert uri == "abfs://custom-container/path/to/file.zarr"


class TestGetAzureFilesystem:
    """Tests for get_azure_filesystem function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("adlfs.AzureBlobFileSystem")
    def test_get_filesystem_creates_adlfs(self, mock_adlfs_class):
        """Test that get_azure_filesystem creates AzureBlobFileSystem."""
        from oceanstream.echodata.storage import get_azure_filesystem

        mock_fs = MagicMock()
        mock_adlfs_class.return_value = mock_fs

        fs = get_azure_filesystem()

        mock_adlfs_class.assert_called_once_with(connection_string="test-conn")
        assert fs == mock_fs

    @patch.dict("os.environ", {}, clear=True)
    @patch("oceanstream.storage.manager.load_storage_configuration")
    def test_get_filesystem_no_credentials_raises(self, mock_load_config):
        """Test that missing credentials raises ValueError."""
        from oceanstream.echodata.storage import get_azure_filesystem

        # Mock the storage configuration to return empty providers
        mock_config = MagicMock()
        mock_config.providers = {}
        mock_load_config.return_value = mock_config

        with pytest.raises(ValueError, match="Azure credentials not found"):
            get_azure_filesystem()


class TestGetAzureZarrStore:
    """Tests for get_azure_zarr_store function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("oceanstream.echodata.storage.get_azure_filesystem")
    @patch("zarr.storage.FSStore")
    def test_get_zarr_store(self, mock_fsstore, mock_get_fs):
        """Test getting a Zarr store for Azure."""
        from oceanstream.echodata.storage import get_azure_zarr_store

        mock_fs = MagicMock()
        mock_get_fs.return_value = mock_fs
        mock_store = MagicMock()
        mock_fsstore.return_value = mock_store

        store = get_azure_zarr_store("echodata/test/file.zarr")

        mock_fsstore.assert_called_once_with(
            "oceanstream-data/echodata/test/file.zarr", fs=mock_fs, mode="w"
        )
        assert store == mock_store

    @patch.dict(
        "os.environ",
        {"AZURE_CONNECTION_STRING": "conn", "AZURE_CONTAINER_NAME": "custom"},
        clear=True,
    )
    @patch("oceanstream.echodata.storage.get_azure_filesystem")
    @patch("zarr.storage.FSStore")
    def test_get_zarr_store_custom_container(self, mock_fsstore, mock_get_fs):
        """Test getting Zarr store with custom container."""
        from oceanstream.echodata.storage import get_azure_zarr_store

        mock_get_fs.return_value = MagicMock()
        mock_fsstore.return_value = MagicMock()

        get_azure_zarr_store("path/file.zarr", container="override")

        args = mock_fsstore.call_args
        assert args[0][0] == "override/path/file.zarr"


class TestSaveEchodataToAzure:
    """Tests for save_echodata_to_azure function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("oceanstream.echodata.storage.get_azure_zarr_store")
    @patch("oceanstream.echodata.storage.get_zarr_store_uri")
    def test_save_echodata(self, mock_uri, mock_store):
        """Test saving EchoData to Azure."""
        from oceanstream.echodata.storage import save_echodata_to_azure

        mock_echodata = MagicMock()
        mock_echodata.source_file = "/path/to/raw_file.raw"
        mock_store.return_value = MagicMock()
        mock_uri.return_value = "abfs://container/echodata/campaign/converted/raw_file.zarr"

        result = save_echodata_to_azure(mock_echodata, campaign_id="campaign")

        mock_echodata.to_zarr.assert_called_once()
        assert result == "abfs://container/echodata/campaign/converted/raw_file.zarr"

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("oceanstream.echodata.storage.get_azure_zarr_store")
    @patch("oceanstream.echodata.storage.get_zarr_store_uri")
    def test_save_echodata_custom_filename(self, mock_uri, mock_store):
        """Test saving EchoData with custom filename."""
        from oceanstream.echodata.storage import save_echodata_to_azure

        mock_echodata = MagicMock()
        mock_echodata.source_file = None
        mock_store.return_value = MagicMock()
        mock_uri.return_value = "abfs://container/echodata/camp/converted/custom.zarr"

        result = save_echodata_to_azure(
            mock_echodata, campaign_id="camp", filename="custom"
        )

        mock_store.assert_called_once()
        assert "custom" in result


class TestSaveSvToAzure:
    """Tests for save_sv_to_azure function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("oceanstream.echodata.storage.get_azure_zarr_store")
    @patch("oceanstream.echodata.storage.get_zarr_store_uri")
    def test_save_sv_dataset(self, mock_uri, mock_store):
        """Test saving Sv dataset to Azure."""
        from oceanstream.echodata.storage import save_sv_to_azure

        mock_sv = MagicMock()
        mock_store.return_value = MagicMock()
        mock_uri.return_value = "abfs://container/echodata/camp/calibrated/file_Sv.zarr"

        result = save_sv_to_azure(mock_sv, campaign_id="camp", filename="file")

        mock_sv.to_zarr.assert_called_once()
        assert "_Sv" in result


class TestSaveProductToAzure:
    """Tests for save_product_to_azure function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("oceanstream.echodata.storage.get_azure_zarr_store")
    @patch("oceanstream.echodata.storage.get_zarr_store_uri")
    def test_save_mvbs_product(self, mock_uri, mock_store):
        """Test saving MVBS product to Azure."""
        from oceanstream.echodata.storage import save_product_to_azure

        mock_ds = MagicMock()
        mock_store.return_value = MagicMock()
        mock_uri.return_value = (
            "abfs://container/echodata/camp/products/file_mvbs.zarr"
        )

        result = save_product_to_azure(
            mock_ds, campaign_id="camp", filename="file", product_type="mvbs"
        )

        mock_ds.to_zarr.assert_called_once()
        assert "_mvbs" in result


class TestOpenSvFromAzure:
    """Tests for open_sv_from_azure function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("xarray.open_zarr")
    def test_open_sv_dataset(self, mock_open_zarr):
        """Test opening Sv dataset from Azure."""
        from oceanstream.echodata.storage import open_sv_from_azure

        mock_ds = MagicMock()
        mock_open_zarr.return_value = mock_ds

        result = open_sv_from_azure(campaign_id="camp", filename="file")

        mock_open_zarr.assert_called_once()
        call_kwargs = mock_open_zarr.call_args.kwargs
        assert "storage_options" in call_kwargs
        assert result == mock_ds


class TestListCampaignData:
    """Tests for list_campaign_data function."""

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("oceanstream.echodata.storage.get_azure_filesystem")
    def test_list_campaign_data(self, mock_get_fs):
        """Test listing campaign data from Azure."""
        from oceanstream.echodata.storage import list_campaign_data

        mock_fs = MagicMock()
        mock_fs.ls.return_value = [
            "container/echodata/camp/converted/file1.zarr",
            "container/echodata/camp/converted/file2.zarr",
        ]
        mock_fs.isdir.return_value = False
        mock_get_fs.return_value = mock_fs

        results = list_campaign_data("camp", stage="converted")

        assert len(results) == 2
        assert all(".zarr" in r for r in results)

    @patch.dict("os.environ", {"AZURE_CONNECTION_STRING": "test-conn"}, clear=True)
    @patch("oceanstream.echodata.storage.get_azure_filesystem")
    def test_list_campaign_data_not_found(self, mock_get_fs):
        """Test listing campaign data when path doesn't exist."""
        from oceanstream.echodata.storage import list_campaign_data

        mock_fs = MagicMock()
        mock_fs.ls.side_effect = FileNotFoundError("Not found")
        mock_get_fs.return_value = mock_fs

        results = list_campaign_data("nonexistent")

        assert results == []


class TestModuleExports:
    """Test that all storage functions are properly exported."""

    def test_storage_functions_in_echodata_module(self):
        """Test that storage functions are exported from oceanstream.echodata."""
        from oceanstream import echodata

        assert hasattr(echodata, "get_azure_zarr_store")
        assert hasattr(echodata, "save_echodata_to_azure")
        assert hasattr(echodata, "save_sv_to_azure")
        assert hasattr(echodata, "save_product_to_azure")
        assert hasattr(echodata, "open_sv_from_azure")
        assert hasattr(echodata, "is_azure_configured")
        assert hasattr(echodata, "list_campaign_data")

    def test_storage_functions_callable(self):
        """Test that storage functions are callable."""
        from oceanstream.echodata.storage import (
            get_azure_zarr_store,
            save_echodata_to_azure,
            save_sv_to_azure,
            save_product_to_azure,
            is_azure_configured,
            list_campaign_data,
            build_echodata_path,
            get_zarr_store_uri,
        )

        assert callable(get_azure_zarr_store)
        assert callable(save_echodata_to_azure)
        assert callable(save_sv_to_azure)
        assert callable(save_product_to_azure)
        assert callable(is_azure_configured)
        assert callable(list_campaign_data)
        assert callable(build_echodata_path)
        assert callable(get_zarr_store_uri)
