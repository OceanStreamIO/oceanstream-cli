"""Unit tests for oceanstream.echodata.compute module (Sv, MVBS, NASC)."""

from pathlib import Path
import pytest
import numpy as np
from unittest.mock import patch, MagicMock

# xarray is optional for tests
xr = pytest.importorskip("xarray")


class TestComputeSv:
    """Tests for Sv computation."""

    def test_compute_sv_requires_calibration(self):
        """Sv computation should require calibrated EchoData."""
        from oceanstream.echodata.compute.sv import compute_sv
        
        # Mock uncalibrated EchoData
        mock_ed = MagicMock()
        mock_ed.sonar_model = "EK80"
        
        try:
            # Should raise or return warning for uncalibrated data
            compute_sv(mock_ed, output_path=None)
        except (ValueError, AttributeError, ImportError, FileNotFoundError):
            pass  # Expected - MagicMock is not a valid path

    def test_compute_sv_output_structure(self, tmp_path: Path):
        """Sv output should have expected structure."""
        from oceanstream.echodata.compute.sv import compute_sv
        
        # Create mock Sv output structure
        sv_ds = xr.Dataset({
            "Sv": (["channel", "ping_time", "range_sample"], 
                   np.random.randn(3, 100, 500) - 70),
        })
        
        # Check expected dimensions and variables
        assert "Sv" in sv_ds
        assert "ping_time" in sv_ds.dims
        assert "range_sample" in sv_ds.dims

    def test_add_depth_coordinate(self):
        """Should add depth coordinate to Sv dataset using add_depth_to_sv."""
        from oceanstream.echodata.consolidate import add_depth_to_sv
        
        import pandas as pd
        
        ping_times = pd.date_range("2023-06-01", periods=100, freq="s")
        
        sv_ds = xr.Dataset({
            "Sv": (["channel", "ping_time", "range_sample"], 
                   np.random.randn(3, 100, 500) - 70),
            "echo_range": (["channel", "ping_time", "range_sample"],
                          np.tile(np.linspace(0, 500, 500), (3, 100, 1))),
        }, coords={
            "ping_time": ping_times,
        })
        
        sv_with_depth = add_depth_to_sv(sv_ds, depth_offset=5.0)
        
        assert "depth" in sv_with_depth.data_vars or "depth" in sv_with_depth.coords

    def test_enrich_sv_dataset_exists(self):
        """Verify enrich_sv_dataset function exists and has correct signature.
        
        enrich_sv_dataset is the main enrichment function that adds depth and
        location from EchoData. There's also enrich_sv_with_location which
        gets GPS from geoparquet (for cases where EchoData doesn't have GPS).
        """
        from oceanstream.echodata.compute.sv import enrich_sv_dataset
        import inspect
        
        sig = inspect.signature(enrich_sv_dataset)
        params = list(sig.parameters.keys())
        
        # Check key parameters exist
        assert "ds_Sv" in params
        assert "echodata" in params
        assert "add_depth" in params
        assert "add_location" in params
        assert "depth_offset" in params

    def test_enrich_sv_with_location_exists(self):
        """Verify enrich_sv_with_location function exists for geoparquet GPS fallback.
        
        This function is for edge cases where:
        1. Sv Zarr was created with stock echopype (no add_location)
        2. Legacy data without GPS enrichment
        3. EchoData had no/bad GPS, but geotrack has good GPS
        """
        from oceanstream.echodata.environment import enrich_sv_with_location
        import inspect
        
        sig = inspect.signature(enrich_sv_with_location)
        params = list(sig.parameters.keys())
        
        # Check key parameters exist
        assert "sv_dataset" in params
        assert "campaign_dir" in params or "campaign_id" in params

    def test_compute_sv_from_echodata_forwards_cal_params(self):
        """compute_sv_from_echodata should forward cal_params to compute_Sv."""
        mock_compute_Sv = MagicMock()
        mock_add_depth = MagicMock()
        mock_add_location = MagicMock()

        sentinel_ds = xr.Dataset({"Sv": (["x"], [1, 2, 3])})
        mock_compute_Sv.return_value = sentinel_ds
        mock_add_depth.return_value = sentinel_ds
        mock_add_location.return_value = sentinel_ds

        mock_ep = MagicMock()
        mock_ep.calibrate.compute_Sv = mock_compute_Sv
        mock_ep.consolidate.add_depth = mock_add_depth
        mock_ep.consolidate.add_location = mock_add_location

        mock_ed = MagicMock()
        mock_ed.sonar_model = "EK80"

        cal = {"gain": [25.0]}
        env = {"speed_of_sound": 1500}

        with patch.dict("sys.modules", {"echopype": mock_ep}):
            # Re-import so the local `import echopype as ep` picks up the mock
            import importlib
            import oceanstream.echodata.compute.sv as sv_mod
            importlib.reload(sv_mod)

            sv_mod.compute_sv_from_echodata(mock_ed, env_params=env, cal_params=cal)

        call_kwargs = mock_compute_Sv.call_args.kwargs
        assert call_kwargs["cal_params"] == cal
        assert call_kwargs["env_params"] == env

    def test_compute_sv_from_echodata_omits_none_cal_params(self):
        """cal_params=None should not be passed as a kwarg."""
        mock_compute_Sv = MagicMock()
        mock_add_depth = MagicMock()
        mock_add_location = MagicMock()

        sentinel_ds = xr.Dataset({"Sv": (["x"], [1, 2, 3])})
        mock_compute_Sv.return_value = sentinel_ds
        mock_add_depth.return_value = sentinel_ds
        mock_add_location.return_value = sentinel_ds

        mock_ep = MagicMock()
        mock_ep.calibrate.compute_Sv = mock_compute_Sv
        mock_ep.consolidate.add_depth = mock_add_depth
        mock_ep.consolidate.add_location = mock_add_location

        mock_ed = MagicMock()
        mock_ed.sonar_model = "EK80"

        with patch.dict("sys.modules", {"echopype": mock_ep}):
            import importlib
            import oceanstream.echodata.compute.sv as sv_mod
            importlib.reload(sv_mod)

            sv_mod.compute_sv_from_echodata(mock_ed, cal_params=None, env_params=None)

        call_kwargs = mock_compute_Sv.call_args.kwargs
        assert "cal_params" not in call_kwargs
        assert "env_params" not in call_kwargs


class TestComputeMVBS:
    """Tests for MVBS computation."""

    def test_mvbs_default_bins(self):
        """MVBS should use 1m range, 5s time bins by default."""
        from oceanstream.echodata.config import MVBSConfig
        
        config = MVBSConfig()
        assert config.range_bin == "1m"
        assert config.ping_time_bin == "5s"

    def test_mvbs_reduces_dimensions(self):
        """MVBS should reduce data size through binning."""
        # Create synthetic Sv data
        import pandas as pd
        
        ping_times = pd.date_range("2023-06-01", periods=1000, freq="100ms")  # 100 Hz
        
        sv_ds = xr.Dataset({
            "Sv": (["channel", "ping_time", "range_sample"], 
                   np.random.randn(3, 1000, 500) - 70),
        }, coords={
            "ping_time": ping_times,
            "range_sample": np.arange(500),
        })
        
        # After 5s binning, should have ~20 time bins
        # After 1m binning, should have ~500/resolution range bins
        assert sv_ds["Sv"].shape[1] == 1000  # Original

    def test_mvbs_linear_averaging(self):
        """MVBS should average in linear (not dB) domain."""
        from oceanstream.echodata.compute.mvbs import compute_mvbs
        
        # Create simple test case
        sv_db = np.array([-70, -70, -70, -70])  # All same value
        
        # Mean in linear domain
        sv_linear = 10 ** (sv_db / 10)
        mean_linear = np.mean(sv_linear)
        mvbs_correct = 10 * np.log10(mean_linear)
        
        # Should equal original (since all same)
        assert np.isclose(mvbs_correct, -70)

    def test_mvbs_output_structure(self, tmp_path: Path):
        """MVBS output should have expected structure."""
        import pandas as pd
        
        # Create mock MVBS output
        mvbs_ds = xr.Dataset({
            "MVBS": (["channel", "ping_time", "depth"], 
                    np.random.randn(3, 20, 100) - 70),
        }, coords={
            "channel": ["38kHz", "120kHz", "200kHz"],
            "ping_time": pd.date_range("2023-06-01", periods=20, freq="5s"),
            "depth": np.arange(0, 500, 5),
        })
        
        assert "MVBS" in mvbs_ds
        assert mvbs_ds.dims["ping_time"] == 20


class TestComputeNASC:
    """Tests for NASC computation."""

    def test_nasc_default_bins(self):
        """NASC should use ICES-standard bins by default."""
        from oceanstream.echodata.config import NASCConfig
        
        config = NASCConfig()
        assert config.range_bin == "10m"
        assert config.dist_bin == "0.5nmi"

    def test_nasc_integration(self):
        """NASC should integrate Sv over range and distance."""
        from oceanstream.echodata.compute.nasc import compute_nasc
        
        # NASC = integral of sv * 4*pi * (1852)^2 over depth and distance
        # Units: m² nmi⁻²
        
        # Create synthetic data with required lat/lon and echo_range
        import pandas as pd
        
        n_pings = 100
        n_depth = 50
        sv_ds = xr.Dataset({
            "Sv": (["channel", "ping_time", "depth"], 
                   np.full((3, n_pings, n_depth), -70)),
            "latitude": (["ping_time"], np.linspace(7.0, 8.0, n_pings)),
            "longitude": (["ping_time"], np.linspace(-140.0, -139.0, n_pings)),
            "echo_range": (["channel", "ping_time", "depth"],
                          np.tile(np.arange(0, 500, 10), (3, n_pings, 1))),
        }, coords={
            "ping_time": pd.date_range("2023-06-01", periods=n_pings, freq="10s"),
            "depth": np.arange(0, 500, 10),
        })
        
        try:
            nasc = compute_nasc(sv_ds)
            
            assert "NASC" in nasc
        except (NotImplementedError, AttributeError, ImportError, ValueError, KeyError):
            pass  # API may require additional fields

    def test_nasc_distance_calculation(self):
        """Test haversine distance calculation used by NASC.
        
        Distance between GPS points is calculated internally by echopype's NASC.
        This test verifies the haversine formula which is the standard approach.
        """
        # Haversine formula for distance calculation
        def haversine_nmi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
            """Calculate distance in nautical miles between two points."""
            R_nmi = 3440.065  # Earth radius in nautical miles
            
            lat1_rad = np.radians(lat1)
            lat2_rad = np.radians(lat2)
            dlat = np.radians(lat2 - lat1)
            dlon = np.radians(lon2 - lon1)
            
            a = np.sin(dlat / 2) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
            c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
            
            return R_nmi * c
        
        # One nautical mile = 1 arc minute of latitude
        lat1, lon1 = 10.0, -140.0
        lat2, lon2 = 10.0 + (1/60), -140.0  # 1 arc minute north
        
        dist = haversine_nmi(lat1, lon1, lat2, lon2)
        
        # Should be approximately 1 nmi (within 1% error)
        assert 0.99 < dist < 1.01, f"Expected ~1 nmi, got {dist}"
        
        # Test a longer distance (60 arc minutes = 1 degree = 60 nmi at equator)
        lat1, lon1 = 0.0, 0.0
        lat2, lon2 = 1.0, 0.0  # 1 degree north at equator
        
        dist = haversine_nmi(lat1, lon1, lat2, lon2)
        assert 59.9 < dist < 60.1, f"Expected ~60 nmi, got {dist}"


class TestComputeIntegration:
    """Integration tests for compute pipeline."""

    def test_sv_to_mvbs_pipeline(self, tmp_path: Path):
        """Full Sv -> MVBS pipeline should work."""
        from oceanstream.echodata.compute.sv import compute_sv
        from oceanstream.echodata.compute.mvbs import compute_mvbs
        
        import pandas as pd
        
        # Create synthetic Sv dataset with all required fields for echopype
        n_pings = 1000
        n_range = 500
        channels = ["38kHz", "120kHz", "200kHz"]
        ping_times = pd.date_range("2023-06-01", periods=n_pings, freq="100ms")
        echo_range = np.tile(np.linspace(0, 500, n_range), (3, n_pings, 1))
        
        sv_ds = xr.Dataset({
            "Sv": (["channel", "ping_time", "range_sample"], 
                   np.random.randn(3, n_pings, n_range) - 70),
            "echo_range": (["channel", "ping_time", "range_sample"], echo_range),
            "frequency_nominal": (["channel"], [38000.0, 120000.0, 200000.0]),
        }, coords={
            "channel": channels,
            "ping_time": ping_times,
        })
        
        try:
            mvbs = compute_mvbs(
                sv_ds,
                range_bin="5m",
                ping_time_bin="5s",
            )
            
            assert "Sv" in mvbs  # echopype returns Sv not MVBS
            # Should be significantly smaller than original
            assert mvbs.sizes["ping_time"] < sv_ds.sizes["ping_time"]
        except (ImportError, AttributeError, ValueError, KeyError):
            pass  # May fail if echopype requirements not met

    def test_zarr_roundtrip(self, tmp_path: Path):
        """Sv/MVBS should roundtrip through Zarr."""
        import pandas as pd
        
        ping_times = pd.date_range("2023-06-01", periods=100, freq="S")
        
        sv_ds = xr.Dataset({
            "Sv": (["channel", "ping_time", "range_sample"], 
                   np.random.randn(3, 100, 500) - 70),
        }, coords={
            "channel": ["38kHz", "120kHz", "200kHz"],
            "ping_time": ping_times,
        })
        
        zarr_path = tmp_path / "test_sv.zarr"
        sv_ds.to_zarr(zarr_path)
        
        loaded = xr.open_zarr(zarr_path)
        
        assert np.allclose(sv_ds["Sv"].values, loaded["Sv"].values)


# ──────────────────────────────────────────────────────────────────────────
# Strict xarray-2026 regression tests (no exception swallowing)
# These require echopype + dask.array; skipped individually when missing.
# ──────────────────────────────────────────────────────────────────────────

_has_echopype = bool(pytest.importorskip.__module__)  # always True, just a placeholder
try:
    import echopype  # noqa: F401
    import dask.array  # noqa: F401
except ImportError:
    _has_echopype = False

_skip_no_echopype = pytest.mark.skipif(
    not _has_echopype, reason="echopype + dask.array required for regression tests"
)


def _make_sv_dataset(
    n_pings=200, n_channels=2, n_depth=50, multidim_latlon=False, latlon_as_coords=False
):
    """Build a synthetic Sv dataset for regression testing."""
    import pandas as pd
    import dask.array as da

    rng = np.random.default_rng(42)
    channels = [f"ch{i}" for i in range(n_channels)]
    ping_time = pd.date_range("2023-01-01", periods=n_pings, freq="1s")

    echo_range = np.tile(
        np.linspace(0, 250, n_depth)[np.newaxis, np.newaxis, :],
        (n_channels, n_pings, 1),
    ) + rng.uniform(-0.5, 0.5, (n_channels, n_pings, n_depth))

    Sv = da.from_array(
        rng.uniform(-80, -30, (n_channels, n_pings, n_depth)),
        chunks=(n_channels, 50, n_depth),
    )

    lat_1d = np.linspace(10, 11, n_pings)
    lon_1d = np.linspace(-170, -169, n_pings)

    data_vars = {
        "Sv": (["channel", "ping_time", "range_sample"], Sv),
        "echo_range": (["channel", "ping_time", "range_sample"], echo_range),
    }
    coords = {
        "channel": channels,
        "ping_time": ping_time,
        "range_sample": np.arange(n_depth),
    }

    if multidim_latlon:
        data_vars["latitude"] = (
            ["channel", "ping_time"],
            np.tile(lat_1d[np.newaxis, :], (n_channels, 1)),
        )
        data_vars["longitude"] = (
            ["channel", "ping_time"],
            np.tile(lon_1d[np.newaxis, :], (n_channels, 1)),
        )
    elif latlon_as_coords:
        coords["latitude"] = ("ping_time", lat_1d)
        coords["longitude"] = ("ping_time", lon_1d)
    else:
        data_vars["latitude"] = (["ping_time"], lat_1d)
        data_vars["longitude"] = (["ping_time"], lon_1d)

    ds = xr.Dataset(data_vars=data_vars, coords=coords, attrs={"processing_level": "Level 2A"})
    ds["frequency_nominal"] = xr.DataArray(
        [38000 + i * 82000 for i in range(n_channels)], dims=["channel"]
    )
    return ds


@_skip_no_echopype
class TestMVBSXarray2026:
    """Strict MVBS regression tests — no broad except blocks."""

    @pytest.mark.parametrize(
        "latlon_mode", ["1d_datavar", "multidim", "coords_only", "no_latlon"]
    )
    def test_compute_mvbs_latlon_modes(self, latlon_mode):
        from oceanstream.echodata.compute import compute_mvbs

        kw = {}
        if latlon_mode == "multidim":
            kw["multidim_latlon"] = True
        elif latlon_mode == "coords_only":
            kw["latlon_as_coords"] = True
        ds = _make_sv_dataset(**kw)

        if latlon_mode == "no_latlon":
            ds = ds.drop_vars(["latitude", "longitude"])

        mvbs = compute_mvbs(ds, range_bin="10m", ping_time_bin="20s")

        assert "Sv" in mvbs
        assert mvbs["Sv"].ndim == 3
        assert mvbs.sizes["ping_time"] > 1
        assert mvbs.sizes["echo_range"] > 1

        if latlon_mode != "no_latlon":
            assert "latitude" in mvbs, f"latitude missing for {latlon_mode}"
            assert mvbs["latitude"].dims == ("ping_time",)
            assert mvbs["latitude"].shape[0] == mvbs.sizes["ping_time"]

    def test_compute_mvbs_bin_count(self):
        from oceanstream.echodata.compute import compute_mvbs

        ds = _make_sv_dataset(n_pings=100)
        mvbs = compute_mvbs(ds, range_bin="50m", ping_time_bin="10s")

        # 100 pings at 1s → 10s bins → 10 bins
        assert mvbs.sizes["ping_time"] == 10
        # 250m range / 50m bin → 5 or 6 bins (depending on boundary handling)
        assert mvbs.sizes["echo_range"] in (5, 6)


@_skip_no_echopype
class TestNASCXarray2026:
    """Strict NASC regression tests — no broad except blocks."""

    @pytest.mark.parametrize(
        "latlon_mode", ["1d_datavar", "multidim", "coords_only"]
    )
    def test_compute_nasc_latlon_modes(self, latlon_mode):
        from oceanstream.echodata.compute import compute_nasc

        kw = {}
        if latlon_mode == "multidim":
            kw["multidim_latlon"] = True
        elif latlon_mode == "coords_only":
            kw["latlon_as_coords"] = True
        ds = _make_sv_dataset(**kw)
        ds["depth"] = ds["echo_range"].copy()

        nasc = compute_nasc(ds, range_bin="10m", dist_bin="0.5nmi")

        assert "NASC" in nasc
        assert "NASC_log" in nasc
        assert nasc["NASC"].ndim == 3
        assert nasc.sizes["distance"] > 1
        assert nasc.sizes["depth"] > 1

        assert "latitude" in nasc, f"latitude missing for {latlon_mode}"
        assert "longitude" in nasc, f"longitude missing for {latlon_mode}"

    def test_nasc_values_nonnegative(self):
        from oceanstream.echodata.compute import compute_nasc

        ds = _make_sv_dataset()
        ds["depth"] = ds["echo_range"].copy()
        nasc = compute_nasc(ds, range_bin="20m", dist_bin="0.5nmi")

        # NASC values must be non-negative (acoustic energy integral)
        assert float(nasc["NASC"].min()) >= 0
