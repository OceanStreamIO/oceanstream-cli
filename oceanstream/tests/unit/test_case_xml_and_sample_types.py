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

    @staticmethod
    def _vendor_ed(decimation):
        vendor = xr.Dataset({"WBT_decimation": ("channel", [decimation])},
                            coords={"channel": ["WBT 38"]})
        return _Ed({"Vendor_specific": vendor})

    def test_files_with_other_filter_settings_are_combined_apart(self):
        # EX2506 2025-08-29: the WBT settings change during the day, so echopype refuses
        # to combine all of the day's files.
        groups = {"power": [(f"f{i}", self._vendor_ed(d)) for i, d in enumerate([6, 6, 3, 6, 3])]}
        parts = pfr._combine_parts(groups, "2025-08-29", "short_pulse")
        assert [(suffix, [p for p, _ in files]) for suffix, files in parts] == [
            ("-1", ["f0", "f1", "f3"]), ("-2", ["f2", "f4"])]

    def test_files_with_another_drop_keel_offset_are_combined_apart(self):
        # HB2202 2022-03-01: the drop keel is moved during the day.
        def ed(offset):
            e = self._vendor_ed(6)
            e["Platform"] = xr.Dataset(
                {"drop_keel_offset": ((), offset),
                 "latitude": ("time1", [41.0, 41.1])},
                coords={"time1": pd.date_range("2022-03-01", periods=2, freq="1h")},
            )
            return e

        groups = {"power": [("f0", ed(0.0)), ("f1", ed(0.0)), ("f2", ed(1.1)), ("f3", ed(1.1))]}
        parts = pfr._combine_parts(groups, "2022-03-01", "short_pulse")
        assert [(suffix, [p for p, _ in files]) for suffix, files in parts] == [
            ("-1", ["f0", "f1"]), ("-2", ["f2", "f3"])]

    def test_files_combine_in_recording_order_when_the_prefix_changes(self):
        # HB2401 2024-02-15: the prefix changes at 18:32, and name order put it first.
        paths = ["/x/D20240215-T183215.zarr", "/x/HB-D20240215-T173938.zarr",
                 "/x/D20240215-T235228.zarr", "/x/HB-D20240215-T145956.zarr"]
        assert [Path(p).name for p in sorted(paths, key=pfr._recording_order)] == [
            "HB-D20240215-T145956.zarr", "HB-D20240215-T173938.zarr",
            "D20240215-T183215.zarr", "D20240215-T235228.zarr"]

    def test_settings_split_within_each_sample_type(self):
        groups = {"complex": [("c0", self._vendor_ed(6)), ("c1", self._vendor_ed(6))],
                  "power": [("p0", self._vendor_ed(6)), ("p1", self._vendor_ed(3))]}
        parts = pfr._combine_parts(groups, "2025-08-29", "short_pulse")
        assert [suffix for suffix, _ in parts] == ["-complex", "-power-1", "-power-2"]
        one = pfr._combine_parts({"power": groups["complex"]}, "2025-08-29", "short_pulse")
        assert [suffix for suffix, _ in one] == [""]


class TestPlatformTransducerDepth:
    def test_drop_keel_offset_is_the_transducer_depth(self):
        # HB2202 2022-03-01: TransducerOffsetZ and water level 0, the drop keel at 6.0 m.
        plat = xr.Dataset({"water_level": ((), 0.0), "drop_keel_offset": ((), 6.0),
                           "transducer_offset_z": ("channel", [0.0, 0.0])},
                          coords={"channel": ["WBT 18", "WBT 38"]})
        assert pfr._platform_transducer_depth(_Ed({"Platform": plat})) == 6.0

    def test_no_offsets_gives_none(self):
        plat = xr.Dataset({"water_level": ((), 0.0), "transducer_offset_z": ("channel", [0.0])},
                          coords={"channel": ["WBT 38"]})
        assert pfr._platform_transducer_depth(_Ed({"Platform": plat})) is None
        assert pfr._platform_transducer_depth(_Ed({"Platform": None})) is None


class TestFillMissingPositions:
    def test_pings_without_a_fix_get_interpolated_positions(self):
        t = pd.date_range("2021-07-21T00:00", periods=5, freq="1s")
        ds = xr.Dataset(
            {"latitude": ("ping_time", [66.0, np.nan, 66.2, np.nan, 66.4]),
             "longitude": ("ping_time", [-20.0, np.nan, -20.2, np.nan, -20.4])},
            coords={"ping_time": t},
        )
        out = pfr._fill_missing_positions(ds)
        np.testing.assert_allclose(out["latitude"].values, [66.0, 66.1, 66.2, 66.3, 66.4])
        np.testing.assert_allclose(out["longitude"].values, [-20.0, -20.1, -20.2, -20.3, -20.4])

    def test_join_keeps_a_repeated_ping_once(self):
        def part(times):
            t = pd.to_datetime(times)
            return xr.Dataset(
                {"Sv": (("channel", "ping_time", "range_sample"), np.zeros((1, len(t), 2)))},
                coords={"channel": ["WBT 38"], "ping_time": t, "range_sample": [0, 1]},
            )

        a = part(["2021-07-21T00:00:00", "2021-07-21T00:00:01"])
        b = part(["2021-07-21T00:00:01", "2021-07-21T00:00:02"])  # opens with a's last ping
        joined = pfr._join_sv_parts([a, b])
        assert joined.sizes["ping_time"] == 3
        assert joined.indexes["ping_time"].is_unique



class TestBroadbandFilesAmongCw:
    """The SWFSC Saildrones record one FM file a day among their CW files."""

    @staticmethod
    def _echodata(transmit_type):
        beam = xr.Dataset(
            {"transmit_duration_nominal": (("channel", "ping_time"), [[1.024e-3]]),
             "transmit_type": (("channel", "ping_time"), [[transmit_type]])},
            coords={"channel": ["WBT 38"], "ping_time": pd.to_datetime(["2022-07-20T08:00"]),
                    "frequency_nominal": ("channel", [38000.0])},
        )
        return {"Sonar/Beam_group1": beam}

    def test_fm_pings_are_broadband(self):
        assert pfr._detect_pulse_category(self._echodata("LFM")) == "broadband"
        assert pfr._detect_pulse_category(self._echodata("CW")) == "short_pulse"

    def _combine(self, monkeypatch, expected):
        names = ["D20220720-T065958", "D20220720-T075958", "D20220720-T085959"]
        cats = ["short_pulse", "broadband", "short_pulse"]
        monkeypatch.setattr(pfr, "group_files_by_day", lambda files, n: {"2022-07-20": files})
        combined = {}
        monkeypatch.setattr(pfr, "combine_echodata_day", lambda ed_zarr_paths, day_key, category,
                            echodata_dir: combined.setdefault(category, ed_zarr_paths))
        cfg = pfr.PipelineConfig()
        cfg.expected_categories, cfg.category_parallel, cfg.raw.sv_per_file = expected, False, False
        pfr.run_echodata_combine([(c, f"{n}.zarr", n) for c, n in zip(cats, names)],
                                 [(n, {"file_name": n}) for n in names], cfg, Path("."))
        return combined

    def test_fm_file_is_left_out_of_the_cw_day(self, monkeypatch):
        combined = self._combine(monkeypatch, ["short_pulse"])
        assert combined == {"short_pulse": ["D20220720-T065958.zarr", "D20220720-T085959.zarr"]}

    def test_fm_files_are_combined_when_asked_for(self, monkeypatch):
        combined = self._combine(monkeypatch, ["short_pulse", "broadband"])
        assert combined["broadband"] == ["D20220720-T075958.zarr"]


def test_bin_sv_range_keeps_later_pings_of_a_channel_already_cut():
    """An EK80 on auto range: the first 50 pings need not reach as far as later ones."""
    import numpy as np
    import xarray as xr

    n_pings, n_samples = 60, 8
    sv = np.full((1, n_pings, n_samples), np.nan)
    sv[0, :50, :4] = -60.0
    sv[0, 50:, :8] = -60.0
    # echopype leaves echo_range NaN where the samples are.
    echo_range = np.where(np.isnan(sv), np.nan, np.arange(n_samples) * 0.05)
    ds = xr.Dataset({"Sv": (("channel", "ping_time", "range_sample"), sv),
                     "echo_range": (("channel", "ping_time", "range_sample"), echo_range)},
                    coords={"channel": ["a"], "ping_time": np.arange(n_pings),
                            "range_sample": np.arange(n_samples)})
    assert pfr._bin_sv_range(ds, 0.1, cut_padding=False).sizes["range_sample"] == 4
    # The shared-grid cut keeps what any ping recorded (NF2409 2024-08-21: 43 m, then 228 m).
    out = pfr._bin_sv_range(ds, 0.1)
    assert out.sizes["range_sample"] == n_samples // 2
    assert out["Sv"].isel(ping_time=-1).notnull().all()


def test_bin_sv_range_cuts_the_padding_of_a_coarser_channel():
    """A channel sampled 4 times coarser is NaN-padded on the shared grid; its padding goes."""
    n_pings, n_samples = 5, 16
    spacing = np.array([0.025, 0.1])[:, None, None]
    recorded = np.array([16, 4])[:, None, None]
    idx = np.arange(n_samples)[None, None, :]
    echo_range = np.broadcast_to(np.where(idx < recorded, idx * spacing, np.nan),
                                 (2, n_pings, n_samples)).copy()
    sv = np.where(np.isnan(echo_range), np.nan, -60.0)
    dims = ("channel", "ping_time", "range_sample")
    ds = xr.Dataset({"Sv": (dims, sv), "echo_range": (dims, echo_range)},
                    coords={"channel": ["fine", "coarse"], "ping_time": np.arange(n_pings),
                            "range_sample": np.arange(n_samples)})
    out = pfr._bin_sv_range(ds, 0.1)
    assert out.sizes["range_sample"] == 4  # 16 fine samples / 4, and the 4 coarse ones
    assert out["Sv"].notnull().all()


def test_bin_sv_range_averages_the_absorption_loss_like_the_range():
    """Sv corrected with casts keeps its absorption loss on the binned grid."""
    r = np.broadcast_to(np.arange(1, 9) * 0.05, (1, 3, 8)).copy()
    dims = ("channel", "ping_time", "range_sample")
    ds = xr.Dataset({"Sv": (dims, np.full(r.shape, -60.0)), "echo_range": (dims, r),
                     "absorption_loss": (dims, 2 * 0.07 * r)},
                    coords={"channel": ["a"], "ping_time": np.arange(3),
                            "range_sample": np.arange(8)})
    out = pfr._bin_sv_range(ds, 0.1, cut_padding=False)
    assert out.sizes["range_sample"] == r.shape[-1] // 2
    np.testing.assert_allclose(out["absorption_loss"], 2 * 0.07 * out["echo_range"])


def test_cast_corrected_sv_carries_the_loss_it_was_compensated_for():
    """Sv rises by exactly what its absorption_loss adds over 2 x sound_absorption x range."""
    from types import SimpleNamespace  # noqa: PLC0415

    from oceanstream.echodata.environment.ctd_casts import cast_profiles  # noqa: PLC0415

    t0 = np.datetime64("2023-10-09T00:00", "ns")
    pressure = np.arange(0.0, 1001.0, 2.0)
    warm_dbar = 100.0  # 29 °C above, 8 °C below
    temp = np.where(pressure < warm_dbar, 29.0, 8.0)[None, :].repeat(2, 0)
    casts = xr.Dataset({
        "ctd_temperature": (("N_PROF", "N_LEVELS"), temp),
        "ctd_salinity": (("N_PROF", "N_LEVELS"), np.full(temp.shape, 35.0)),
        "pressure": (("N_PROF", "N_LEVELS"), np.tile(pressure, (2, 1))),
        "time": (("N_PROF",), np.array([t0, t0 + np.timedelta64(1, "h")])),
        "latitude": (("N_PROF",), np.zeros(2)), "longitude": (("N_PROF",), np.full(2, -166.0)),
        "btm_depth": (("N_PROF",), np.full(2, 5000.0)),
    })
    profiles = cast_profiles(casts, [200000.0], max_depth_m=400)
    r = np.broadcast_to(np.array([10.0, 50.0, 100.0, 200.0, 300.0]), (1, 3, 5)).copy()
    times = t0 + np.arange(3) * np.timedelta64(20, "m")
    dims = ("channel", "ping_time", "range_sample")
    sv_ch = xr.Dataset({"Sv": (dims, np.full(r.shape, -70.0)), "echo_range": (dims, r),
                        "sound_absorption": (("ping_time", "channel"), np.full((3, 1), 0.07))},
                       coords={"channel": ["a"], "ping_time": times, "range_sample": np.arange(5)})
    cfg = SimpleNamespace(raw=SimpleNamespace(depth_offset=0.0))
    out = pfr._correct_absorption(sv_ch.copy(), profiles, 200000.0, np.full(3, 0.07), cfg)
    assert out["absorption_loss"].dims == out["Sv"].dims
    np.testing.assert_allclose(out["Sv"] - sv_ch["Sv"],
                               out["absorption_loss"] - 2 * 0.07 * sv_ch["echo_range"], atol=1e-9)
    per_metre = (out["absorption_loss"] / (2 * sv_ch["echo_range"])).isel(channel=0, ping_time=0)
    assert np.all(np.diff(per_metre.values) < 0)  # the warm top 100 m absorbs more


class TestPassiveChannels:
    """Falkor (too) records its 333 kHz channel in passive mode, out to about 6 km."""

    class _EchoData(dict):
        @property
        def group_paths(self):
            return list(self)

        @property
        def beam(self):
            return self.get("Sonar/Beam_group1")

    @classmethod
    def _echodata(cls, modes, samples):
        """Channels with *modes* per ping (0 active, 1 passive) recording *samples* each."""
        channels = [f"WBT {i}" for i in range(len(modes))]
        n_pings, n_max = len(modes[0]), max(samples)
        data = np.full((len(modes), n_pings, n_max, 4), np.nan, dtype=np.float32)
        for c, n in enumerate(samples):
            data[c, :, :n] = 1.0
        times = pd.date_range("2026-03-05", periods=n_pings, freq="10s")
        coords = {"channel": channels, "ping_time": times, "range_sample": np.arange(n_max),
                  "beam": np.arange(4)}
        beam = xr.Dataset(
            {"backscatter_r": (("channel", "ping_time", "range_sample", "beam"), data),
             "backscatter_i": (("channel", "ping_time", "range_sample", "beam"), data.copy()),
             "channel_mode": (("channel", "ping_time"), np.asarray(modes, dtype=float)),
             "frequency_nominal": ("channel", np.arange(len(modes), dtype=float) * 1e5 + 18e3)},
            coords=coords,
        )
        vendor = xr.Dataset({"gain_correction": ("channel", np.zeros(len(modes)))},
                            coords={"channel": channels})
        return cls._EchoData({"Sonar/Beam_group1": beam, "Vendor_specific": vendor,
                              "Environment": xr.Dataset({"sound_speed_indicative": ((), 1500.0)})})

    def test_a_channel_passive_on_every_ping_is_dropped_and_the_range_cut(self):
        ed = self._echodata([[0, 0], [0, 0], [1, 1]], samples=[240, 180, 3350])
        ed, dropped = pfr._drop_passive_channels(ed)
        assert dropped == ["WBT 2"]
        assert list(ed["Sonar/Beam_group1"]["channel"].values) == ["WBT 0", "WBT 1"]
        assert list(ed["Vendor_specific"]["channel"].values) == ["WBT 0", "WBT 1"]
        assert ed["Sonar/Beam_group1"].sizes["range_sample"] == 240

    def test_passive_pings_of_an_active_channel_are_blanked(self):
        ed = self._echodata([[0, 1, 0], [0, 0, 0]], samples=[50, 50])
        ed, dropped = pfr._drop_passive_channels(ed)
        assert dropped == []
        data = ed["Sonar/Beam_group1"]["backscatter_r"].values
        assert np.isnan(data[0, 1]).all()
        assert np.isfinite(data[0, [0, 2], :50]).all() and np.isfinite(data[1]).all()

    def test_channels_pinging_in_turn_are_kept(self):
        # Multiplexed channels have no mode (NaN) on the other channels' pings.
        ed = self._echodata([[0, np.nan, np.nan], [np.nan, 0, np.nan], [np.nan, np.nan, 0]],
                            samples=[60, 50, 40])
        ed, dropped = pfr._drop_passive_channels(ed)
        assert dropped == []
        assert ed["Sonar/Beam_group1"].sizes == {"channel": 3, "ping_time": 3, "range_sample": 60,
                                                 "beam": 4}

    def test_a_file_without_channel_modes_is_left_alone(self):
        ed = self._echodata([[1, 1]], samples=[10])
        ed["Sonar/Beam_group1"] = ed["Sonar/Beam_group1"].drop_vars("channel_mode")
        out, dropped = pfr._drop_passive_channels(ed)
        assert dropped == [] and out["Sonar/Beam_group1"].sizes["channel"] == 1
