"""Tests for sound speed and absorption from CTD casts (environment/ctd_casts.py)."""

import gsw
import numpy as np
import pytest
import xarray as xr
from echopype.calibrate.env_params import harmonize_env_param_time, sanitize_user_env_dict

from oceanstream.echodata.environment.absorption import francois_garrison_absorption
from oceanstream.echodata.environment.ctd_casts import (
    absorption_correction,
    cast_profiles,
    channel_environment,
)
from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed

T0 = np.datetime64("2022-06-21T00:00", "ns")
HOUR = np.timedelta64(3600, "s")
#: The test casts are warm above this pressure (dbar) and 4 °C below.
LAYER_DBAR = 100
PRESSURE = np.arange(0.0, 3001.0, 2.0)


def casts(surface_temps=(25.0, 20.0), max_pressures=(3000.0, 3000.0),
          bottoms=(5000.0, 5000.0), hours=(0, 12)):
    """CCHDO-layout casts: a warm layer above 4 °C water, salinity 35, latitude 30° N."""
    n = len(surface_temps)
    temp = np.empty((n, PRESSURE.size))
    for i, ts in enumerate(surface_temps):
        temp[i] = np.where(PRESSURE < LAYER_DBAR, ts, 4.0)
    sal = np.full_like(temp, 35.0)
    for i, pmax in enumerate(max_pressures):
        temp[i, PRESSURE > pmax] = np.nan
        sal[i, PRESSURE > pmax] = np.nan
    return xr.Dataset(
        {
            "ctd_temperature": (("N_PROF", "N_LEVELS"), temp),
            "ctd_salinity": (("N_PROF", "N_LEVELS"), sal),
            "pressure": (("N_PROF", "N_LEVELS"), np.tile(PRESSURE, (n, 1))),
            "time": (("N_PROF",), np.array([T0 + h * HOUR for h in hours])),
            "latitude": (("N_PROF",), np.full(n, 30.0)),
            "longitude": (("N_PROF",), np.full(n, -150.0)),
            "btm_depth": (("N_PROF",), np.asarray(bottoms, dtype=float)),
        }
    )


def one_cast(surface_temp=25.0, max_pressure=3000.0, bottom=5000.0):
    return casts(surface_temps=(surface_temp,), max_pressures=(max_pressure,),
                 bottoms=(bottom,), hours=(0,))


def path_means(surface_temp, top, length, frequency):
    """Harmonic-mean sound speed and mean absorption from *top* to *top* + *length*."""
    z = np.arange(top, top + length + 0.05, 0.1)
    z_of_p = -gsw.z_from_p(PRESSURE, 30.0)
    t = np.interp(z, z_of_p, np.where(PRESSURE < LAYER_DBAR, surface_temp, 4.0))
    s = np.full_like(z, 35.0)
    c = mackenzie_sound_speed(t, s, z)
    a = np.asarray(francois_garrison_absorption(
        frequency_hz=frequency, temperature=t, salinity=s, depth=z, pH=8.0))
    return length / np.trapezoid(1.0 / c, z), np.trapezoid(a, z) / length


class TestChannelEnvironment:
    def test_means_over_the_channel_range(self):
        profiles = cast_profiles(one_cast(), [38000.0], max_depth_m=2100)
        c, a = channel_environment(profiles, 38000.0, np.array([T0]), max_range_m=1500,
                                   transducer_depth_m=5)
        c_ref, a_ref = path_means(25.0, 5, 1500, 38000.0)
        assert c[0] == pytest.approx(c_ref, abs=0.05)
        assert a[0] == pytest.approx(a_ref, rel=2e-3)

    def test_interpolates_between_casts_and_holds_at_the_ends(self):
        profiles = cast_profiles(casts(), [200000.0], max_depth_m=400)
        times = np.array([T0 - HOUR, T0, T0 + 6 * HOUR, T0 + 12 * HOUR, T0 + 30 * HOUR])
        c, a = channel_environment(profiles, 200000.0, times, max_range_m=300)
        assert c[0] == c[1] and c[3] == c[4]
        assert c[2] == pytest.approx((c[1] + c[3]) / 2)
        assert a[2] == pytest.approx((a[1] + a[3]) / 2)
        assert c[1] != pytest.approx(c[3])  # the casts differ in the warm layer

    def test_a_shallow_cast_in_deep_water_is_not_used(self):
        # The first cast stops at 1000 dbar in 5000 m of water; the second is full depth.
        profiles = cast_profiles(casts(surface_temps=(30.0, 20.0),
                                       max_pressures=(1000.0, 3000.0)),
                                 [18000.0], max_depth_m=2100)
        c, _ = channel_environment(profiles, 18000.0, np.array([T0]), max_range_m=2000)
        c_full, _ = channel_environment(profiles, 18000.0, np.array([T0 + 12 * HOUR]),
                                        max_range_m=2000)
        assert c[0] == c_full[0]

    def test_a_cast_to_the_seabed_is_used_in_shallow_water(self):
        profiles = cast_profiles(one_cast(30.0, max_pressure=950.0, bottom=1000.0),
                                 [18000.0], max_depth_m=2100)
        c, a = channel_environment(profiles, 18000.0, np.array([T0]), max_range_m=2000)
        assert np.isfinite(c[0]) and np.isfinite(a[0])

    def test_no_usable_cast_says_what_to_do(self):
        profiles = cast_profiles(one_cast(30.0, max_pressure=500.0), [18000.0],
                                 max_depth_m=2100)
        with pytest.raises(ValueError, match="full-depth casts"):
            channel_environment(profiles, 18000.0, np.array([T0]), max_range_m=2000)

    def test_flagged_levels_are_ignored(self):
        clean = one_cast()
        spiked = clean.copy(deep=True)
        spiked["ctd_temperature"][0, 100] = 40.0
        flags = np.full(spiked["ctd_temperature"].shape, 2.0)
        flags[0, 100] = 4.0
        spiked["ctd_temperature_qc"] = (("N_PROF", "N_LEVELS"), flags)
        a = cast_profiles(clean, [120000.0], max_depth_m=600)
        b = cast_profiles(spiked, [120000.0], max_depth_m=600)
        np.testing.assert_allclose(a.absorption[120000.0], b.absorption[120000.0])


class TestAbsorptionCorrection:
    def test_matches_the_path_mean_at_every_range(self):
        profiles = cast_profiles(one_cast(), [200000.0], max_depth_m=400)
        r = np.array([[10.0, 50.0, 150.0, 300.0]])
        corr = absorption_correction(profiles, 200000.0, np.array([T0]), r, np.array([0.06]),
                                     transducer_depth_m=5)
        expected = [2 * x * (path_means(25.0, 5, x, 200000.0)[1] - 0.06) for x in r[0]]
        np.testing.assert_allclose(corr[0], expected, atol=0.01)

    def test_zero_at_full_range_with_the_channel_mean(self):
        profiles = cast_profiles(casts(), [120000.0], max_depth_m=600)
        times = np.array([T0, T0 + 4 * HOUR])
        _, used = channel_environment(profiles, 120000.0, times, max_range_m=500,
                                      transducer_depth_m=5)
        r = np.tile(np.array([100.0, 500.0]), (2, 1))
        corr = absorption_correction(profiles, 120000.0, times, r, used, transducer_depth_m=5)
        np.testing.assert_allclose(corr[:, 1], 0.0, atol=1e-6)
        # Over the first 100 m the warm layer weighs more than over the full 500 m.
        assert np.all(np.abs(corr[:, 0]) > 0.1)  # noqa: PLR2004

    def test_nan_where_there_is_no_range(self):
        profiles = cast_profiles(casts(), [38000.0], max_depth_m=1600)
        r = np.array([[100.0, np.nan]])
        corr = absorption_correction(profiles, 38000.0, np.array([T0]), r, np.array([0.009]))
        assert np.isfinite(corr[0, 0]) and np.isnan(corr[0, 1])


def test_echopype_takes_the_values_per_ping():
    profiles = cast_profiles(casts(), [38000.0], max_depth_m=1600)
    pings = np.array([T0 + k * 1800 * np.timedelta64(1, "s") for k in range(5)])
    c, a = channel_environment(profiles, 38000.0, pings, max_range_m=1500)
    ch = ["WBT 1-ES38"]
    coords = {"channel": ch, "time1": pings}
    env = {
        "sound_speed": xr.DataArray(c[None, :], dims=("channel", "time1"), coords=coords),
        "sound_absorption": xr.DataArray(a[None, :], dims=("channel", "time1"), coords=coords),
    }
    clean = sanitize_user_env_dict(env, channel=ch)
    ping_time = xr.DataArray(pings, dims="ping_time", coords={"ping_time": pings})
    for name, values in (("sound_speed", c), ("sound_absorption", a)):
        out = harmonize_env_param_time(clean[name], ping_time=ping_time)
        np.testing.assert_allclose(out.sel(channel=ch[0]).values, values)
