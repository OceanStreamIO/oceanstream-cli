"""Tests for oceanstream.echodata.utils.encoding.

Pure xarray tests — no echopype dependency.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from oceanstream.echodata.utils.encoding import fix_chunking, get_variable_encoding


class TestGetVariableEncoding:
    """Tests for NetCDF4 encoding dict generation."""

    def test_numeric_vars_get_zlib(self):
        ds = xr.Dataset({
            "temperature": xr.DataArray([1.0, 2.0, 3.0]),
            "salinity": xr.DataArray([35.0, 35.1, 35.2]),
        })
        enc = get_variable_encoding(ds)
        for var in ("temperature", "salinity"):
            assert enc[var]["zlib"] is True
            assert enc[var]["complevel"] == 5

    def test_string_vars_get_empty_encoding(self):
        ds = xr.Dataset({
            "name": xr.DataArray(["a", "b", "c"]),
            "value": xr.DataArray([1.0, 2.0, 3.0]),
        })
        enc = get_variable_encoding(ds)
        assert enc["name"] == {}
        assert enc["value"]["zlib"] is True

    def test_custom_compression_level(self):
        ds = xr.Dataset({"x": xr.DataArray([1.0])})
        enc = get_variable_encoding(ds, compression_level=9)
        assert enc["x"]["complevel"] == 9

    def test_empty_dataset(self):
        ds = xr.Dataset()
        enc = get_variable_encoding(ds)
        assert enc == {}

    def test_integer_vars_get_zlib(self):
        ds = xr.Dataset({"counts": xr.DataArray([1, 2, 3])})
        enc = get_variable_encoding(ds)
        assert enc["counts"]["zlib"] is True


class TestFixChunking:
    """Tests for Zarr chunk hint harmonisation."""

    def test_tiny_array_gets_encoding_cleared(self):
        ds = xr.Dataset({"small": xr.DataArray(np.arange(10))})
        ds["small"].encoding["chunks"] = (5,)
        result = fix_chunking(ds, tiny_limit=100)
        assert "chunks" not in result["small"].encoding

    def test_large_numpy_array_mismatched_chunks_dropped(self):
        """Large non-dask array with stale chunk hint → hint is dropped."""
        data = np.arange(20_000, dtype=float)
        ds = xr.Dataset({"big": xr.DataArray(data)})
        ds["big"].encoding["chunks"] = (1000,)
        result = fix_chunking(ds, tiny_limit=10_000)
        # No dask chunks → stale hint removed
        assert "chunks" not in result["big"].encoding

    def test_large_array_without_chunks_unchanged(self):
        data = np.arange(20_000, dtype=float)
        ds = xr.Dataset({"big": xr.DataArray(data)})
        result = fix_chunking(ds, tiny_limit=10_000)
        np.testing.assert_array_equal(result["big"].values, data)
