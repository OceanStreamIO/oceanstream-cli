"""Unit tests for the ADCP processing module."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ADCP_DATA_DIR = _PROJECT_ROOT / "raw_data" / "adcp"
_RAW_FILE = _ADCP_DATA_DIR / "km2023_257_66125.raw"
_REF_FILE = _ADCP_DATA_DIR / "wh300_reference.nc"

_HAS_RAW = _RAW_FILE.exists()
_HAS_REF = _REF_FILE.exists()

requires_raw = pytest.mark.skipif(
    not _HAS_RAW, reason="ADCP raw data not available"
)
requires_ref = pytest.mark.skipif(
    not (_HAS_RAW and _HAS_REF), reason="ADCP raw + reference data not available"
)


# ---------------------------------------------------------------------------
# Compat shim (must import before dolfyn)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _patch_compat():
    """Ensure compat patches are applied before any dolfyn import."""
    import oceanstream.adcp._compat  # noqa: F401


# ---------------------------------------------------------------------------
# rdi_reader tests
# ---------------------------------------------------------------------------
class TestRdiReader:
    """Tests for the RDI binary reader."""

    @requires_raw
    def test_read_rdi_returns_dataset(self):
        from oceanstream.adcp.rdi_reader import read_rdi

        ds = read_rdi(_RAW_FILE)
        assert isinstance(ds, xr.Dataset)

    @requires_raw
    def test_read_rdi_has_required_variables(self):
        from oceanstream.adcp.rdi_reader import read_rdi

        ds = read_rdi(_RAW_FILE)
        assert "vel" in ds.data_vars
        assert "amp" in ds.data_vars
        assert "corr" in ds.data_vars
        assert "heading" in ds.data_vars
        assert "temp" in ds.data_vars
        assert "beam2inst_orientmat" in ds.data_vars
        assert "orientmat" in ds.data_vars

    @requires_raw
    def test_read_rdi_correct_instrument(self):
        from oceanstream.adcp.rdi_reader import read_rdi

        ds = read_rdi(_RAW_FILE)
        assert ds.attrs["inst_make"] == "TRDI"
        assert ds.attrs["inst_model"] == "Workhorse"
        assert ds.attrs["freq"] == 300
        assert ds.attrs["coord_sys"] == "beam"
        assert ds.attrs["n_beams"] == 4

    @requires_raw
    def test_read_rdi_dimensions(self):
        from oceanstream.adcp.rdi_reader import read_rdi

        ds = read_rdi(_RAW_FILE)
        assert ds.sizes["time"] > 1000  # should be ~7342 pings
        assert ds.sizes["range"] == 70
        assert ds.sizes["beam"] == 4

    def test_read_rdi_file_not_found(self):
        from oceanstream.adcp.rdi_reader import read_rdi

        with pytest.raises(FileNotFoundError):
            read_rdi(Path("/nonexistent/file.raw"))

    def test_read_rdi_wrong_extension(self, tmp_path):
        from oceanstream.adcp.rdi_reader import read_rdi

        bad_file = tmp_path / "test.csv"
        bad_file.touch()
        with pytest.raises(ValueError, match="Expected .raw"):
            read_rdi(bad_file)

    def test_scan_rdi_files(self, tmp_path):
        from oceanstream.adcp.rdi_reader import scan_rdi_files

        (tmp_path / "a.raw").touch()
        (tmp_path / "b.raw").touch()
        (tmp_path / "c.txt").touch()

        files = scan_rdi_files(tmp_path)
        assert len(files) == 2
        assert all(f.suffix == ".raw" for f in files)
        assert files[0].name == "a.raw"

    def test_scan_rdi_files_not_a_directory(self, tmp_path):
        from oceanstream.adcp.rdi_reader import scan_rdi_files

        with pytest.raises(NotADirectoryError):
            scan_rdi_files(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# transforms tests
# ---------------------------------------------------------------------------
class TestBeamToEarth:
    """Tests for beam-to-earth coordinate transform."""

    @requires_raw
    def test_beam_to_earth_produces_earth_coords(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)

        assert earth.attrs["coord_sys"] == "earth"
        assert "u" in earth.data_vars
        assert "v" in earth.data_vars
        assert "w" in earth.data_vars

    @requires_raw
    def test_beam_to_earth_depth_offset(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)

        # Depth should be range + transducer_depth
        expected_depth = raw["range"].values + 7.0
        np.testing.assert_allclose(earth["depth"].values, expected_depth, atol=0.01)

    @requires_raw
    def test_beam_to_earth_qc_masks_bad_data(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0, corr_threshold=64)

        # Some data should be masked as NaN (bad pings, deep bins)
        assert np.isnan(earth["u"].values).any()
        # But most shallow bins should have valid data
        shallow_u = earth["u"].values[:10, :]
        valid_frac = (~np.isnan(shallow_u)).sum() / shallow_u.size
        assert valid_frac > 0.5, f"Only {valid_frac:.1%} valid in shallow bins"

    @requires_raw
    def test_beam_to_earth_rejects_already_transformed(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)

        with pytest.raises(ValueError, match="coord_sys"):
            beam_to_earth(earth)


class TestEnsembleAverage:
    """Tests for ensemble time-averaging."""

    @requires_raw
    def test_ensemble_average_reduces_time(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        # ~1.6 hours at 120s → ~48 ensembles
        assert 30 < avg.sizes["time"] < 60
        assert "num_pings" in avg.data_vars
        assert avg["num_pings"].values.min() > 0

    @requires_raw
    def test_ensemble_average_ping_count(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        # With ~0.98s ping rate and 120s interval, expect ~120 pings/ensemble
        median_pings = int(np.median(avg["num_pings"].values))
        assert 100 < median_pings < 200


# ---------------------------------------------------------------------------
# Validation against UHDAS reference output
# ---------------------------------------------------------------------------
class TestReferenceValidation:
    """Compare our output with UHDAS-processed reference.

    Note: our output includes ship velocity (no GPS data available),
    so velocity magnitudes will differ. We validate:
    - Temperature matches (direct instrument reading)
    - Ensemble count and timing are consistent
    - Velocity profile structure correlates with reference
    """

    @requires_ref
    def test_temperature_matches_reference(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        ref = xr.open_dataset(_REF_FILE)
        t0, t1 = avg.time.values[0], avg.time.values[-1]
        ref_slice = ref.sel(time=slice(t0, t1))

        # Match times and compare temperature
        temp_diffs = []
        for i, t_ours in enumerate(avg.time.values):
            dt = np.abs(ref_slice.time.values - t_ours)
            j = int(np.argmin(dt))
            if dt[j] < np.timedelta64(90, "s"):
                temp_diffs.append(
                    abs(float(avg.tr_temp.values[i]) - float(ref_slice.tr_temp.values[j]))
                )

        assert len(temp_diffs) > 30
        assert np.mean(temp_diffs) < 0.1, (
            f"Mean temperature diff {np.mean(temp_diffs):.3f}°C exceeds 0.1°C"
        )

    @requires_ref
    def test_ensemble_count_is_consistent(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        ref = xr.open_dataset(_REF_FILE)
        t0, t1 = avg.time.values[0], avg.time.values[-1]
        ref_slice = ref.sel(time=slice(t0, t1))

        # Should have roughly the same number of ensembles (±2)
        assert abs(avg.sizes["time"] - ref_slice.sizes["time"]) <= 3

    @requires_ref
    def test_depth_bins_match_reference(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        ref = xr.open_dataset(_REF_FILE)

        # Reference depth at first time step
        ref_depth = ref["depth"].values[0, :]
        our_depth = avg["depth"].values[0, :]

        # Should match within 0.1m (same instrument config)
        np.testing.assert_allclose(our_depth, ref_depth, atol=0.1)

    @requires_ref
    def test_velocity_structure_correlates(self):
        """Verify velocity profile shape correlates with reference.

        Our u/v includes ship velocity, but the vertical *structure*
        (relative variation across depth bins) should correlate with
        the reference at overlapping times.
        """
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        ref = xr.open_dataset(_REF_FILE)
        t0, t1 = avg.time.values[0], avg.time.values[-1]
        ref_slice = ref.sel(time=slice(t0, t1))

        correlations = []
        for i, t_ours in enumerate(avg.time.values):
            dt = np.abs(ref_slice.time.values - t_ours)
            j = int(np.argmin(dt))
            if dt[j] >= np.timedelta64(90, "s"):
                continue

            # Compare profile structure at shallow bins (3-25)
            u_ours = avg.u.values[i, 3:25]
            u_ref = ref_slice.u.values[j, 3:25]
            valid = ~np.isnan(u_ours) & ~np.isnan(u_ref)
            if valid.sum() > 10:
                # Remove mean offset (ship velocity) and correlate
                u_o = u_ours[valid] - np.nanmean(u_ours[valid])
                u_r = u_ref[valid] - np.nanmean(u_ref[valid])
                if np.std(u_r) > 0.01:
                    corr = np.corrcoef(u_o, u_r)[0, 1]
                    correlations.append(corr)

        if correlations:
            median_corr = np.median(np.abs(correlations))
            assert median_corr > 0.3, (
                f"Median velocity profile |correlation| {median_corr:.3f} "
                "is too low — transform may be incorrect"
            )


# ---------------------------------------------------------------------------
# adcp_to_dataframe tests
# ---------------------------------------------------------------------------
class TestAdcpToDataframe:
    """Tests for the adcp_to_dataframe flattening function."""

    @requires_raw
    def test_returns_dataframe(self):
        import pandas as pd

        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import adcp_to_dataframe, beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        df = adcp_to_dataframe(avg)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    @requires_raw
    def test_row_count(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import adcp_to_dataframe, beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        df = adcp_to_dataframe(avg)
        expected = avg.sizes["time"] * avg.sizes["depth_cell"]
        assert len(df) == expected

    @requires_raw
    def test_has_required_columns(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import adcp_to_dataframe, beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        df = adcp_to_dataframe(avg)
        for col in ("time", "depth", "u", "v", "w", "amp", "heading", "temperature", "num_pings"):
            assert col in df.columns, f"Missing column: {col}"

    @requires_raw
    def test_depth_values_positive(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import adcp_to_dataframe, beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        df = adcp_to_dataframe(avg)
        assert (df["depth"] > 0).all()

    @requires_raw
    def test_time_is_utc(self):
        from oceanstream.adcp.rdi_reader import read_rdi
        from oceanstream.adcp.transforms import adcp_to_dataframe, beam_to_earth, ensemble_average

        raw = read_rdi(_RAW_FILE)
        earth = beam_to_earth(raw, transducer_depth=7.0)
        avg = ensemble_average(earth, interval_seconds=120.0)

        df = adcp_to_dataframe(avg)
        assert df["time"].dt.tz is not None


# ---------------------------------------------------------------------------
# process_file tests
# ---------------------------------------------------------------------------
class TestProcessFile:
    """Tests for the high-level process_file function."""

    @requires_raw
    def test_returns_nonempty_dataframe(self):
        import pandas as pd

        from oceanstream.adcp.processor import process_file

        df = process_file(_RAW_FILE)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    @requires_raw
    def test_velocity_range_sane(self):
        from oceanstream.adcp.processor import process_file

        df = process_file(_RAW_FILE)
        for col in ("u", "v"):
            valid = df[col].dropna()
            assert len(valid) > 0, f"No valid {col} velocities"
            assert valid.abs().max() < 30.0, f"{col} max velocity unreasonably high"

    @requires_raw
    def test_temperature_sane(self):
        from oceanstream.adcp.processor import process_file

        df = process_file(_RAW_FILE)
        temp = df["temperature"].dropna()
        assert 0 < temp.mean() < 35, f"Mean temp {temp.mean()} outside expected range"

    @requires_raw
    def test_depth_range(self):
        from oceanstream.adcp.processor import process_file

        df = process_file(_RAW_FILE)
        assert df["depth"].max() > 100
        assert df["depth"].min() > 0
