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
    def test_get_zarr_store(self, mock_get_fs):
        """Test getting a Zarr store for Azure."""
        import zarr
        from oceanstream.echodata.storage import get_azure_zarr_store

        mock_fs = MagicMock()
        mock_get_fs.return_value = mock_fs

        if hasattr(zarr.storage, "FSStore"):
            mock_store = MagicMock()
            with patch("zarr.storage.FSStore", return_value=mock_store) as mock_fsstore:
                store = get_azure_zarr_store("echodata/test/file.zarr")
                mock_fsstore.assert_called_once_with(
                    "oceanstream-data/echodata/test/file.zarr", fs=mock_fs, mode="w"
                )
                assert store == mock_store
        else:
            mock_mapper = MagicMock()
            mock_fs.get_mapper.return_value = mock_mapper
            store = get_azure_zarr_store("echodata/test/file.zarr")
            mock_fs.get_mapper.assert_called_once_with(
                "oceanstream-data/echodata/test/file.zarr"
            )
            assert store == mock_mapper

    @patch.dict(
        "os.environ",
        {"AZURE_CONNECTION_STRING": "conn", "AZURE_CONTAINER_NAME": "custom"},
        clear=True,
    )
    @patch("oceanstream.echodata.storage.get_azure_filesystem")
    def test_get_zarr_store_custom_container(self, mock_get_fs):
        """Test getting Zarr store with custom container."""
        import zarr
        from oceanstream.echodata.storage import get_azure_zarr_store

        mock_fs = MagicMock()
        mock_get_fs.return_value = mock_fs

        if hasattr(zarr.storage, "FSStore"):
            with patch("zarr.storage.FSStore", return_value=MagicMock()) as mock_fsstore:
                get_azure_zarr_store("path/file.zarr", container="override")
                args = mock_fsstore.call_args
                assert args[0][0] == "override/path/file.zarr"
        else:
            mock_fs.get_mapper.return_value = MagicMock()
            get_azure_zarr_store("path/file.zarr", container="override")
            args = mock_fs.get_mapper.call_args
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


# ═══════════════════════════════════════════════════════════════════════════
# Local storage backend tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLocalStorage:
    """Tests for the local filesystem storage backend.

    Every test in this class resets ``_LOCAL_ROOT`` on teardown to avoid
    leaking state into other tests.
    """

    @pytest.fixture(autouse=True)
    def _reset_local_storage(self, monkeypatch):
        """Reset storage backend after each test."""
        monkeypatch.delenv("OCEANSTREAM_STORAGE_BACKEND", raising=False)
        monkeypatch.delenv("OCEANSTREAM_OUTPUT_DIR", raising=False)
        yield
        from oceanstream.echodata.storage import use_azure_storage

        use_azure_storage()

    # ── Toggle ───────────────────────────────────────────────────

    def test_use_local_storage_toggle(self, tmp_path: Path):
        """use_local_storage / use_azure_storage toggle is_local_storage."""
        from oceanstream.echodata.storage import (
            is_local_storage,
            use_azure_storage,
            use_local_storage,
        )

        assert is_local_storage() is False

        use_local_storage(tmp_path)
        assert is_local_storage() is True

        use_azure_storage()
        assert is_local_storage() is False

    def test_is_local_storage_env_var(self, monkeypatch):
        """OCEANSTREAM_STORAGE_BACKEND=local activates local mode."""
        from oceanstream.echodata.storage import is_local_storage

        monkeypatch.setenv("OCEANSTREAM_STORAGE_BACKEND", "local")
        assert is_local_storage() is True

    # ── get_azure_zarr_store ─────────────────────────────────────

    def test_get_azure_zarr_store_local(self, tmp_path: Path):
        """In local mode, get_azure_zarr_store returns a string path."""
        from oceanstream.echodata.storage import (
            get_azure_zarr_store,
            use_local_storage,
        )

        use_local_storage(tmp_path)
        result = get_azure_zarr_store("data/file.zarr", container="processed")

        assert isinstance(result, str)
        expected = tmp_path / "processed" / "data" / "file.zarr"
        assert result == str(expected)
        # Parent dirs should have been created
        assert expected.parent.exists()

    # ── get_zarr_store_uri ───────────────────────────────────────

    def test_get_zarr_store_uri_local(self, tmp_path: Path):
        """In local mode, get_zarr_store_uri returns a local path."""
        from oceanstream.echodata.storage import (
            get_zarr_store_uri,
            use_local_storage,
        )

        use_local_storage(tmp_path)
        result = get_zarr_store_uri("day/file.zarr", container="output")

        assert "abfs://" not in result
        assert str(tmp_path / "output" / "day" / "file.zarr") == result

    # ── save + open roundtrip ────────────────────────────────────

    def test_save_and_open_roundtrip(self, tmp_path: Path):
        """save_dataset_to_azure + open_sv_from_azure roundtrip in local mode."""
        import numpy as np
        import xarray as xr

        from oceanstream.echodata.storage import (
            open_sv_from_azure,
            save_dataset_to_azure,
            use_local_storage,
        )

        use_local_storage(tmp_path)

        ds = xr.Dataset({
            "Sv": (["ping_time", "range_sample"], np.random.randn(10, 20) - 70),
        })

        zarr_path = "test/roundtrip.zarr"
        returned = save_dataset_to_azure(ds, zarr_path, container="processed")
        assert (tmp_path / "processed" / zarr_path).exists()

        loaded = open_sv_from_azure(zarr_path=zarr_path, container="processed")
        assert "Sv" in loaded
        np.testing.assert_array_almost_equal(
            loaded["Sv"].values, ds["Sv"].values
        )
        loaded.close()

    # ── ensure_container_exists ──────────────────────────────────

    def test_ensure_container_exists_local(self, tmp_path: Path):
        """ensure_container_exists creates a subdirectory in local mode."""
        from oceanstream.echodata.storage import (
            ensure_container_exists,
            use_local_storage,
        )

        use_local_storage(tmp_path)
        ensure_container_exists("my-container")

        assert (tmp_path / "my-container").is_dir()

    # ── upload_file_to_blob ──────────────────────────────────────

    def test_upload_file_to_blob_local(self, tmp_path: Path):
        """upload_file_to_blob copies file to local tree in local mode."""
        from oceanstream.echodata.storage import (
            upload_file_to_blob,
            use_local_storage,
        )

        use_local_storage(tmp_path)

        src = tmp_path / "source.txt"
        src.write_text("hello world")

        upload_file_to_blob(str(src), "dest/file.txt", "container")

        dest = tmp_path / "container" / "dest" / "file.txt"
        assert dest.exists()
        assert dest.read_text() == "hello world"

    # ── _LocalListFS ─────────────────────────────────────────────

    def test_local_list_fs_ls(self, tmp_path: Path):
        """_LocalListFS.ls lists directory contents."""
        from oceanstream.echodata.storage import _LocalListFS

        (tmp_path / "ctr" / "subdir").mkdir(parents=True)
        (tmp_path / "ctr" / "file.txt").write_text("x")

        fs = _LocalListFS(tmp_path)
        items = fs.ls("ctr")

        assert len(items) == 2
        assert any("file.txt" in i for i in items)
        assert any("subdir" in i for i in items)

    def test_local_list_fs_ls_detail(self, tmp_path: Path):
        """_LocalListFS.ls(detail=True) returns dicts with type and size."""
        from oceanstream.echodata.storage import _LocalListFS

        (tmp_path / "ctr").mkdir()
        (tmp_path / "ctr" / "data.bin").write_bytes(b"abc")

        fs = _LocalListFS(tmp_path)
        items = fs.ls("ctr", detail=True)

        assert len(items) == 1
        entry = items[0]
        assert entry["type"] == "file"
        assert entry["size"] == 3
        assert "data.bin" in entry["name"]

    def test_local_list_fs_isdir(self, tmp_path: Path):
        """_LocalListFS.isdir works for dirs and files."""
        from oceanstream.echodata.storage import _LocalListFS

        (tmp_path / "adir").mkdir()
        (tmp_path / "afile").write_text("x")

        fs = _LocalListFS(tmp_path)

        assert fs.isdir("adir") is True
        assert fs.isdir("afile") is False
        assert fs.isdir("nonexistent") is False

    def test_local_list_fs_nonexistent(self, tmp_path: Path):
        """_LocalListFS.ls on nonexistent path returns empty list."""
        from oceanstream.echodata.storage import _LocalListFS

        fs = _LocalListFS(tmp_path)
        assert fs.ls("does-not-exist") == []

    def test_local_list_fs_get_mapper(self, tmp_path: Path):
        """_LocalListFS.get_mapper returns a string path."""
        from oceanstream.echodata.storage import _LocalListFS

        fs = _LocalListFS(tmp_path)
        result = fs.get_mapper("container/path")

        assert result == str(tmp_path / "container" / "path")

    # ── get_azure_filesystem returns _LocalListFS ────────────────

    def test_get_azure_filesystem_local_mode(self, tmp_path: Path):
        """get_azure_filesystem returns _LocalListFS in local mode."""
        from oceanstream.echodata.storage import (
            _LocalListFS,
            get_azure_filesystem,
            use_local_storage,
        )

        use_local_storage(tmp_path)
        fs = get_azure_filesystem()

        assert isinstance(fs, _LocalListFS)

    # ── Module exports for new functions ─────────────────────────

    def test_local_storage_exports(self):
        """New local storage functions are exported from oceanstream.echodata."""
        from oceanstream import echodata

        assert hasattr(echodata, "use_local_storage")
        assert hasattr(echodata, "use_azure_storage")
        assert hasattr(echodata, "is_local_storage")


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
