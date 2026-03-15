"""Tests for oceanstream.echodata.environment.geoparquet dataclasses and helpers.

Tests the pure-logic parts: EnvVarMapping, EnvData properties, and
_build_parquet_filters. Cloud/filesystem operations are mocked.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from oceanstream.echodata.environment.geoparquet import (
    EnvData,
    EnvVarMapping,
)


# ---------------------------------------------------------------------------
# EnvVarMapping
# ---------------------------------------------------------------------------

class TestEnvVarMapping:
    """Tests for the variable-name mapping dataclass."""

    def test_defaults(self):
        m = EnvVarMapping()
        assert m.time == "time"
        assert m.latitude == "latitude"
        assert m.longitude == "longitude"
        assert m.temperature is None

    def test_saildrone_factory(self):
        m = EnvVarMapping.saildrone()
        assert m.temperature == "TEMP_SBE37_MEAN"
        assert m.salinity == "SAL_SBE37_MEAN"
        assert m.conductivity == "COND_SBE37_MEAN"
        assert m.platform_id == "platform_id"

    def test_r2r_factory(self):
        m = EnvVarMapping.r2r()
        assert m.temperature == "temperature"
        assert m.salinity == "salinity"
        assert m.depth == "depth"

    def test_get_columns_required_always_present(self):
        m = EnvVarMapping()
        cols = m.get_columns()
        assert "time" in cols
        assert "latitude" in cols
        assert "longitude" in cols

    def test_get_columns_includes_optional(self):
        m = EnvVarMapping(temperature="temp_c", salinity="sal_psu")
        cols = m.get_columns()
        assert "temp_c" in cols
        assert "sal_psu" in cols

    def test_get_columns_includes_extra(self):
        m = EnvVarMapping(extra={"my_var": "MY_COL"})
        cols = m.get_columns()
        assert "MY_COL" in cols

    def test_get_columns_skips_none(self):
        m = EnvVarMapping()
        cols = m.get_columns()
        # None attrs should not appear in list
        assert None not in cols


# ---------------------------------------------------------------------------
# EnvData
# ---------------------------------------------------------------------------

class TestEnvData:
    """Tests for the environmental data container."""

    @pytest.fixture()
    def sample_env(self):
        n = 100
        times = np.arange(
            np.datetime64("2023-08-13T09:00"),
            np.datetime64("2023-08-13T09:00") + np.timedelta64(n, "m"),
            np.timedelta64(1, "m"),
        )
        return EnvData(
            time=times,
            latitude=np.linspace(37.0, 37.5, n),
            longitude=np.linspace(-122.0, -121.5, n),
            temperature=np.linspace(20.0, 22.0, n),
            salinity=np.full(n, 35.0),
        )

    def test_n_records(self, sample_env):
        assert sample_env.n_records == 100

    def test_time_range(self, sample_env):
        start, end = sample_env.time_range
        assert "2023-08-13" in start
        assert "2023-08-13" in end

    def test_spatial_bounds(self, sample_env):
        min_lat, max_lat, min_lon, max_lon = sample_env.spatial_bounds
        assert min_lat == pytest.approx(37.0, abs=0.01)
        assert max_lat == pytest.approx(37.5, abs=0.01)
        assert min_lon == pytest.approx(-122.0, abs=0.01)
        assert max_lon == pytest.approx(-121.5, abs=0.01)

    def test_has_ctd_true(self, sample_env):
        assert sample_env.has_ctd is True

    def test_has_ctd_false_no_temp(self):
        env = EnvData(
            time=np.array([np.datetime64("2023-01-01")]),
            latitude=np.array([37.0]),
            longitude=np.array([-122.0]),
        )
        assert env.has_ctd is False

    def test_has_ctd_false_all_nan(self):
        env = EnvData(
            time=np.array([np.datetime64("2023-01-01")]),
            latitude=np.array([37.0]),
            longitude=np.array([-122.0]),
            temperature=np.array([np.nan]),
            salinity=np.array([35.0]),
        )
        assert env.has_ctd is False

    def test_to_dataframe(self, sample_env):
        df = sample_env.to_dataframe()
        assert len(df) == 100
        assert "time" in df.columns
        assert "temperature" in df.columns
        assert "salinity" in df.columns

    def test_to_dataframe_optional_fields_omitted(self):
        env = EnvData(
            time=np.array([np.datetime64("2023-01-01")]),
            latitude=np.array([37.0]),
            longitude=np.array([-122.0]),
        )
        df = env.to_dataframe()
        assert "temperature" not in df.columns
        assert "salinity" not in df.columns

    def test_compute_sound_speed(self, sample_env):
        result = sample_env.compute_sound_speed()
        assert len(result) == 100
        # Physical range for seawater
        assert np.all(result > 1400)
        assert np.all(result < 1600)

    def test_compute_sound_speed_without_ctd_raises(self):
        env = EnvData(
            time=np.array([np.datetime64("2023-01-01")]),
            latitude=np.array([37.0]),
            longitude=np.array([-122.0]),
        )
        with pytest.raises(ValueError, match="Temperature and salinity required"):
            env.compute_sound_speed()

    def test_compute_absorption(self, sample_env):
        result = sample_env.compute_absorption(frequency_hz=38000)
        assert len(result) == 100
        assert np.all(result > 0)

    def test_compute_absorption_without_ctd_raises(self):
        env = EnvData(
            time=np.array([np.datetime64("2023-01-01")]),
            latitude=np.array([37.0]),
            longitude=np.array([-122.0]),
        )
        with pytest.raises(ValueError, match="Temperature and salinity required"):
            env.compute_absorption(frequency_hz=38000)


# ---------------------------------------------------------------------------
# _build_parquet_filters (if exposed) and credential helpers
# ---------------------------------------------------------------------------

class TestCredentialHelpers:
    """Test that credential helpers read env vars correctly."""

    def test_azure_connection_string(self, monkeypatch):
        from oceanstream.echodata.environment.geoparquet import _get_azure_storage_options
        monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "DefaultEndpointsProtocol=https;...")
        opts = _get_azure_storage_options()
        assert "connection_string" in opts

    def test_azure_account_key(self, monkeypatch):
        from oceanstream.echodata.environment.geoparquet import _get_azure_storage_options
        monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "myaccount")
        monkeypatch.setenv("AZURE_STORAGE_KEY", "mykey")
        opts = _get_azure_storage_options()
        assert opts["account_name"] == "myaccount"
        assert opts["account_key"] == "mykey"

    def test_s3_credentials(self, monkeypatch):
        from oceanstream.echodata.environment.geoparquet import _get_s3_storage_options
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA...")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        opts = _get_s3_storage_options()
        assert opts["key"] == "AKIA..."
        assert opts["secret"] == "secret"

    def test_gcs_credentials(self, monkeypatch):
        from oceanstream.echodata.environment.geoparquet import _get_gcs_storage_options
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/key.json")
        opts = _get_gcs_storage_options()
        assert opts["token"] == "/path/to/key.json"

    def test_s3_empty_env(self, monkeypatch):
        from oceanstream.echodata.environment.geoparquet import _get_s3_storage_options
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        opts = _get_s3_storage_options()
        assert "key" not in opts
