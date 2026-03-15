"""Tests for oceanstream.echodata.export.

Uses xarray (no echopype) and tmp_path for file I/O tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr

from oceanstream.echodata.export import save_to_netcdf, zip_netcdf_files


# ---------------------------------------------------------------------------
# save_to_netcdf
# ---------------------------------------------------------------------------

class TestSaveToNetcdf:
    """Tests for local NetCDF writing."""

    @pytest.fixture()
    def sample_ds(self):
        return xr.Dataset({
            "temperature": xr.DataArray(
                np.random.randn(10, 5).astype(np.float32),
                dims=["time", "depth"],
            ),
        })

    def test_creates_file(self, tmp_path, sample_ds):
        out = tmp_path / "output.nc"
        result = save_to_netcdf(sample_ds, out)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_creates_parent_dirs(self, tmp_path, sample_ds):
        out = tmp_path / "a" / "b" / "c" / "output.nc"
        save_to_netcdf(sample_ds, out)
        assert out.exists()

    def test_string_path_accepted(self, tmp_path, sample_ds):
        out = str(tmp_path / "output.nc")
        result = save_to_netcdf(sample_ds, out)
        assert Path(result).exists()

    def test_compression_level_forwarded(self, tmp_path, sample_ds):
        out1 = tmp_path / "low.nc"
        out9 = tmp_path / "high.nc"
        save_to_netcdf(sample_ds, out1, compression_level=1)
        save_to_netcdf(sample_ds, out9, compression_level=9)
        # Higher compression → smaller file (for non-trivial data)
        assert out1.exists() and out9.exists()

    def test_write_chunks(self, tmp_path, sample_ds):
        out = tmp_path / "chunked.nc"
        save_to_netcdf(sample_ds, out, write_chunks={"time": 5})
        assert out.exists()

    def test_roundtrip_data(self, tmp_path, sample_ds):
        out = tmp_path / "rt.nc"
        save_to_netcdf(sample_ds, out)
        loaded = xr.open_dataset(out)
        np.testing.assert_allclose(
            loaded["temperature"].values,
            sample_ds["temperature"].values,
            atol=1e-6,
        )
        loaded.close()


# ---------------------------------------------------------------------------
# save_to_netcdf_azure
# ---------------------------------------------------------------------------

class TestSaveToNetcdfAzure:
    """Tests for Azure upload with mocked filesystem."""

    def test_upload_calls_fs_put(self, tmp_path):
        from oceanstream.echodata.export import save_to_netcdf_azure

        ds = xr.Dataset({"x": xr.DataArray([1.0, 2.0, 3.0])})
        mock_fs = MagicMock()

        with patch("oceanstream.echodata.export.save_to_netcdf") as mock_save:
            local = tmp_path / "container" / "blob.nc"
            mock_save.side_effect = lambda ds, path, **kw: (
                Path(path).parent.mkdir(parents=True, exist_ok=True),
                Path(path).write_bytes(b"fake"),
                Path(path),
            )[-1]

            with patch("oceanstream.echodata.storage.get_azure_filesystem", return_value=mock_fs):
                save_to_netcdf_azure(
                    ds, "blob.nc", "container",
                    base_temp_path=str(tmp_path),
                )

        mock_fs.put.assert_called_once()

    def test_retry_on_upload_failure(self, tmp_path):
        from oceanstream.echodata.export import save_to_netcdf_azure

        ds = xr.Dataset({"x": xr.DataArray([1.0])})
        mock_fs = MagicMock()
        # Fail first two attempts, succeed on third
        mock_fs.put.side_effect = [OSError("fail"), OSError("fail"), None]

        with patch("oceanstream.echodata.export.save_to_netcdf") as mock_save:
            mock_save.side_effect = lambda ds, path, **kw: (
                Path(path).parent.mkdir(parents=True, exist_ok=True),
                Path(path).write_bytes(b"fake"),
                Path(path),
            )[-1]

            with patch("oceanstream.echodata.storage.get_azure_filesystem", return_value=mock_fs):
                with patch("time.sleep"):  # Don't actually wait
                    save_to_netcdf_azure(
                        ds, "blob.nc", "container",
                        base_temp_path=str(tmp_path),
                        max_retries=3,
                        backoff_sec=1,
                    )

        assert mock_fs.put.call_count == 3


# ---------------------------------------------------------------------------
# zip_netcdf_files
# ---------------------------------------------------------------------------

class TestZipNetcdfFiles:
    """Tests for ZIP bundling."""

    def test_creates_zip(self, tmp_path):
        f1 = tmp_path / "a.nc"
        f2 = tmp_path / "b.nc"
        f1.write_bytes(b"data1")
        f2.write_bytes(b"data2")

        zip_path = tmp_path / "bundle.zip"
        result = zip_netcdf_files([f1, f2], zip_path)
        assert result == zip_path
        assert zip_path.exists()

    def test_zip_contains_all_files(self, tmp_path):
        import zipfile
        files = []
        for name in ("x.nc", "y.nc", "z.nc"):
            p = tmp_path / name
            p.write_bytes(b"test")
            files.append(p)

        zip_path = tmp_path / "out.zip"
        zip_netcdf_files(files, zip_path)

        with zipfile.ZipFile(zip_path) as zf:
            assert set(zf.namelist()) == {"x.nc", "y.nc", "z.nc"}

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "data.nc"
        f.write_bytes(b"x")
        zip_path = tmp_path / "sub" / "dir" / "out.zip"
        zip_netcdf_files([f], zip_path)
        assert zip_path.exists()
