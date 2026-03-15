"""Tests for oceanstream.echodata.utils.mask_transforms.

All functions are pure numpy/xarray math — no echopype dependency.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from oceanstream.echodata.utils.mask_transforms import (
    downsample,
    lin,
    line_to_square,
    log,
    upsample,
)


# ---------------------------------------------------------------------------
# log / lin conversion
# ---------------------------------------------------------------------------

class TestLog:
    """Tests for linear → dB conversion."""

    def test_positive_values(self):
        da = xr.DataArray([1.0, 10.0, 100.0])
        result = log(da)
        np.testing.assert_allclose(result.values, [0.0, 10.0, 20.0], atol=1e-10)

    def test_zero_maps_to_minus999(self):
        da = xr.DataArray([0.0, 1.0])
        result = log(da)
        assert result.values[0] == -999

    def test_negative_maps_to_minus999(self):
        da = xr.DataArray([-5.0])
        result = log(da)
        assert result.values[0] == -999

    def test_scalar_input(self):
        result = log(100.0)
        assert np.isclose(result, 20.0)

    def test_list_input(self):
        result = log([1.0, 10.0])
        assert isinstance(result, np.ndarray)
        np.testing.assert_allclose(result, [0.0, 10.0], atol=1e-10)

    def test_non_parallelized(self):
        da = xr.DataArray([10.0])
        result = log(da, parallelized=False)
        np.testing.assert_allclose(result.values, [10.0], atol=1e-10)


class TestLin:
    """Tests for dB → linear conversion."""

    def test_basic(self):
        da = xr.DataArray([0.0, 10.0, 20.0])
        result = lin(da)
        np.testing.assert_allclose(result.values, [1.0, 10.0, 100.0], atol=1e-10)

    def test_nan_preserved(self):
        da = xr.DataArray([np.nan, 10.0])
        result = lin(da)
        assert np.isnan(result.values[0])
        np.testing.assert_allclose(result.values[1], 10.0, atol=1e-10)


class TestLogLinRoundtrip:
    """Round-trip tests for log ↔ lin."""

    def test_roundtrip_positive(self):
        original = xr.DataArray([0.5, 1.0, 3.14, 50.0, 1000.0])
        recovered = lin(log(original))
        np.testing.assert_allclose(recovered.values, original.values, rtol=1e-6)


# ---------------------------------------------------------------------------
# downsample
# ---------------------------------------------------------------------------

class TestDownsample:
    """Tests for coarsening DataArrays."""

    @pytest.fixture()
    def sample_da(self):
        return xr.DataArray(
            np.arange(20, dtype=float).reshape(4, 5),
            dims=["x", "y"],
        )

    def test_mean_reduces_shape(self, sample_da):
        result = downsample(sample_da, {"x": 2}, operation="mean")
        assert result.shape[0] == 2

    def test_sum_operation(self, sample_da):
        result = downsample(sample_da, {"x": 2}, operation="sum")
        # sum of rows 0,1 and rows 2,3
        np.testing.assert_allclose(result.values[0], sample_da.values[0] + sample_da.values[1])

    def test_invalid_operation_raises(self, sample_da):
        with pytest.raises(ValueError, match="Unsupported operation"):
            downsample(sample_da, {"x": 2}, operation="max")

    def test_missing_coordinate_raises(self, sample_da):
        with pytest.raises(ValueError, match="not in dataset dimensions"):
            downsample(sample_da, {"z": 2})

    def test_is_log_mode(self):
        """When is_log=True, conversion goes linear→agg→dB."""
        # Create dB values (10 dB = linear 10)
        da = xr.DataArray([10.0, 10.0, 10.0, 10.0], dims=["x"])
        result = downsample(da, {"x": 2}, operation="mean", is_log=True)
        # Mean of linear 10 is 10, back to dB is 10
        np.testing.assert_allclose(result.values, [10.0, 10.0], atol=0.1)


# ---------------------------------------------------------------------------
# upsample
# ---------------------------------------------------------------------------

class TestUpsample:
    """Tests for nearest-neighbour upsampling."""

    def test_shape_matches_target(self):
        source = xr.DataArray([1.0, 5.0], dims=["x"], coords={"x": [0.0, 10.0]})
        target = xr.DataArray(np.zeros(5), dims=["x"], coords={"x": np.linspace(0, 10, 5)})
        result = upsample(source, target)
        assert result.shape == target.shape


# ---------------------------------------------------------------------------
# line_to_square
# ---------------------------------------------------------------------------

class TestLineToSquare:
    """Tests for broadcasting 1-D to 2-D."""

    def test_broadcast_shape(self):
        one = xr.DataArray([1.0, 2.0, 3.0], dims=["a"])
        two = xr.DataArray(np.zeros((3, 4)), dims=["a", "b"])
        result = line_to_square(one, two, "b")
        assert result.shape == (3, 4)

    def test_broadcast_values(self):
        one = xr.DataArray([10.0, 20.0], dims=["a"])
        two = xr.DataArray(np.zeros((2, 3)), dims=["a", "b"])
        result = line_to_square(one, two, "b")
        np.testing.assert_array_equal(result[0], [10.0, 10.0, 10.0])
        np.testing.assert_array_equal(result[1], [20.0, 20.0, 20.0])
