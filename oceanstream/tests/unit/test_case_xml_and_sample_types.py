"""Calibration XML, NMEA positions and days that mix complex and power/angle files."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

from oceanstream.echodata.calibrate.case_xml import case_cal_params, load_case_xml  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "batch_processing"

CAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<calibration>
  <case channel="1" kHz="18" equivalentBeamAngle="-17.11" g="23.67" SA="1024:0.06"/>
<case channel="2" kHz="38" equivalentBeamAngle="-20.30" g="27.03" SA="512:-0.10;1024:-0.04"/>
</calibration>
"""


@contextmanager
def scripts_importable():
    """Import ``scripts/batch_processing`` without leaking ``config`` or the environment."""
    env, path, shadowed = dict(os.environ), list(sys.path), sys.modules.pop("config", None)
    sys.path.insert(0, str(_SCRIPTS))
    try:
        yield
    finally:
        sys.path[:] = path
        sys.modules.pop("config", None)
        if shadowed is not None:
            sys.modules["config"] = shadowed
        os.environ.clear()
        os.environ.update(env)


with scripts_importable():
    import process_from_raw as pfr  # noqa: E402


def _beam(freqs, pulse=1.024e-3):
    channels = [f"WBT {int(f / 1000)}" for f in freqs]
    td = np.full((len(freqs), 3), pulse)
    td[0, 0] = np.nan  # first channel did not transmit in the first ping
    return xr.Dataset(
        {"frequency_nominal": ("channel", np.asarray(freqs, dtype=float)),
         "transmit_duration_nominal": (("channel", "ping_time"), td)},
        coords={"channel": channels, "ping_time": pd.date_range("2021-07-12", periods=3, freq="1s")},
    )


class TestCaseXml:
    def test_load(self, tmp_path):
        path = tmp_path / "cal.xml"
        path.write_text(CAL_XML)
        table = load_case_xml(path)
        assert table[38000.0] == {"gain": 27.03, "eba": -20.30, "sa": {512: -0.10, 1024: -0.04}}
        assert table[18000.0]["sa"] == {1024: 0.06}

    def test_cal_params_follow_frequency_and_pulse(self, tmp_path):
        path = tmp_path / "cal.xml"
        path.write_text(CAL_XML)
        cal = case_cal_params(_beam([38000, 18000]), load_case_xml(path))
        assert list(cal["gain_correction"].channel.values) == ["WBT 38", "WBT 18"]
        np.testing.assert_allclose(cal["gain_correction"].values, [27.03, 23.67])
        np.testing.assert_allclose(cal["equivalent_beam_angle"].values, [-20.30, -17.11])
        np.testing.assert_allclose(cal["sa_correction"].values, [-0.04, 0.06])

    def test_parameter_missing_for_a_channel_is_left_out(self, tmp_path):
        path = tmp_path / "cal.xml"
        path.write_text(CAL_XML)
        assert case_cal_params(_beam([38000, 70000]), load_case_xml(path)) == {}

    def test_no_cases_is_an_error(self, tmp_path):
        path = tmp_path / "cal.xml"
        path.write_text("<calibration/>")
        with pytest.raises(ValueError, match="no <case>"):
            load_case_xml(path)


class _Ed(dict):
    """The EchoData lookups the helpers make: ed["Platform"], ed["Sonar/Beam_group1"]."""


class TestPlatformGps:
    def test_gga_fixes_without_failed_ones(self):
        t = pd.date_range("2021-07-12T00:00", periods=4, freq="1s")
        plat = xr.Dataset(
            {"latitude": ("time1", [66.0, 0.0, 66.1, 66.2]),
             "longitude": ("time1", [-10.0, 0.0, -10.1, -10.2]),
             "sentence_type": ("time1", ["GGA", "GGA", "GLL", "GGA"])},
            coords={"time1": t},
        )
        df = pfr._platform_gps(_Ed({"Platform": plat}))
        assert list(df["lat"]) == [66.0, 66.2]
        assert list(df["dt"]) == [t[0], t[3]]

    def test_no_platform(self):
        assert pfr._platform_gps(_Ed({"Platform": None})) is None


class TestSampleTypes:
    def test_encode_mode(self):
        complex_beam = xr.Dataset({"backscatter_r": ("x", [1.0]), "backscatter_i": ("x", [0.0])})
        power_beam = xr.Dataset({"backscatter_r": ("x", [1.0]), "angle_athwartship": ("x", [0.0])})
        assert pfr._encode_mode_of(_Ed({"Sonar/Beam_group1": complex_beam})) == "complex"
        assert pfr._encode_mode_of(_Ed({"Sonar/Beam_group1": power_beam})) == "power"

    def test_join_sv_parts_in_time_order(self):
        def part(start, n_range):
            t = pd.date_range(start, periods=2, freq="1s")
            return xr.Dataset(
                {"Sv": (("channel", "ping_time", "range_sample"), np.zeros((1, 2, n_range))),
                 "frequency_nominal": ("channel", [38000.0])},
                coords={"channel": ["WBT 38"], "ping_time": t, "range_sample": np.arange(n_range)},
            )

        later, earlier = part("2021-07-09T19:20", 3), part("2021-07-09T19:10", 4)
        joined = pfr._join_sv_parts([later, earlier])
        assert joined.sizes == {"channel": 1, "ping_time": 4, "range_sample": 4}
        assert joined["ping_time"].to_index().is_monotonic_increasing
        assert np.isnan(joined["Sv"].isel(ping_time=3, range_sample=3)).item()
