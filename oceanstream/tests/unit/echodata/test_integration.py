"""Echo integration of denoised Sv: what each kind of removed cell counts as."""

from __future__ import annotations

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

from oceanstream.echodata.products import FLAG_BITS, integration_view  # noqa: E402

BG, SEABED, IMPULSE = FLAG_BITS["background"], FLAG_BITS["seabed"], FLAG_BITS["impulse"]


def _day(n_pings=4, n_range=20, channels=("38",), sv=-60.0):
    """Sv on a 1 m grid from 0 m, one value everywhere, no flags."""
    shape = (len(channels), n_pings, n_range)
    depth = np.broadcast_to(np.arange(n_range, dtype=float), shape).copy()
    return xr.Dataset(
        {
            "Sv": (("channel", "ping_time", "range_sample"), np.full(shape, sv)),
            "echo_range": (("channel", "ping_time", "range_sample"), depth.copy()),
            "depth": (("channel", "ping_time", "range_sample"), depth),
            "denoise_flags": (("channel", "ping_time", "range_sample"), np.zeros(shape, np.uint8)),
            "frequency_nominal": ("channel", [38000.0] * len(channels)),
        },
        coords={
            "channel": list(channels),
            "ping_time": pd.date_range("2021-07-16", periods=n_pings, freq="1s"),
            "range_sample": np.arange(n_range),
        },
    )


def _remove(ds, sel, bit):
    """Mark cells as a step's removal: the bit is set and Sv becomes NaN, as resolved."""
    ds["denoise_flags"][sel] |= bit
    ds["Sv"][sel] = np.nan


class TestIntegrationView:
    def test_each_kind_of_removed_cell(self):
        ds = _day()
        _remove(ds, (0, slice(None), slice(0, 5)), BG)
        _remove(ds, (0, slice(None), slice(5, 7)), IMPULSE)
        _remove(ds, (0, slice(None), slice(7, 9)), IMPULSE | BG)
        _remove(ds, (0, slice(None), slice(15, 20)), SEABED)
        out = integration_view(ds)
        sv, depth = out["Sv"].values[0, 0], out["depth"].values[0, 0]
        assert np.isneginf(sv[:5]).all()  # below the noise: zero
        assert np.isnan(sv[5:9]).all()  # artefacts, also with the background bit: left out
        assert (sv[9:15] == -60.0).all()  # kept cells untouched
        assert np.isnan(sv[15:]).all() and np.isnan(depth[15:]).all()  # seabed: no height
        assert np.isfinite(depth[:15]).all()
        assert "integration" in out.attrs

    def test_sv_without_flags_is_unchanged(self):
        ds = _day().drop_vars("denoise_flags")
        assert integration_view(ds) is ds

    def test_mvbs_counts_cells_below_the_noise_as_zero(self):
        from oceanstream.echodata.compute import compute_mvbs

        ds = _day(n_range=10)
        _remove(ds, (0, slice(None), slice(0, 5)), BG)  # half the bin below the noise
        skipped = compute_mvbs(ds, range_bin="10m", ping_time_bin="10s")["Sv"].values
        zero = compute_mvbs(integration_view(ds), range_bin="10m", ping_time_bin="10s")["Sv"].values
        assert np.nanmax(skipped) == pytest.approx(-60.0)
        assert np.nanmax(zero) == pytest.approx(-60.0 + 10 * np.log10(0.5))  # -63.0 dB

    def test_nasc_counts_only_the_water_above_the_seabed(self):
        import echopype as ep

        ds = _day(n_pings=20, n_range=20)
        _remove(ds, (0, slice(None), slice(15, 20)), SEABED)
        ds["latitude"] = ("ping_time", np.linspace(66.0, 66.02, 20))
        ds["longitude"] = ("ping_time", np.full(20, -17.0))

        def nasc(d):
            out = ep.commongrid.compute_NASC(d, range_bin="20m", dist_bin="5nmi")
            return float(np.nanmax(out["NASC"].values))

        per_m = 1e-6 * 4 * np.pi * 1852**2
        # echopype sums the depth steps of the samples in the bin: 19 for the full 20 m
        # grid, 14 for the 15 samples of water above the seabed.
        assert nasc(ds) == pytest.approx(per_m * 19, rel=1e-3)
        assert nasc(integration_view(ds)) == pytest.approx(per_m * 14, rel=1e-3)


class TestArtefactPruning:
    @staticmethod
    def kept(ds, threshold=0.5):
        from oceanstream.echodata.denoise import drop_noisy_pings

        return drop_noisy_pings(ds, drop_threshold=0.8, artefact_threshold=threshold).sizes["ping_time"]

    def test_empty_water_is_kept(self):
        ds = _day()
        _remove(ds, (0, slice(None), slice(0, 15)), BG)
        _remove(ds, (0, slice(None), slice(15, 20)), SEABED)
        assert self.kept(ds) == 4

    def test_a_ping_of_artefacts_is_dropped(self):
        ds = _day()
        _remove(ds, (0, 1, slice(0, 12)), IMPULSE)  # 12 of 20 cells: 60 %
        _remove(ds, (0, 2, slice(0, 8)), IMPULSE)  # 40 %: kept
        assert self.kept(ds) == 3

    def test_a_ping_without_data_is_dropped(self):
        ds = _day()
        ds["Sv"][0, 3, :] = np.nan  # nothing recorded, nothing flagged
        assert self.kept(ds) == 3

    def test_one_good_channel_keeps_the_ping(self):
        ds = _day(channels=("38", "120"))
        _remove(ds, (0, 1, slice(None)), IMPULSE)
        assert self.kept(ds) == 4

    def test_a_channel_that_did_not_ping_has_no_say(self):
        ds = _day(channels=("38", "120"))
        ds["Sv"][1, :, :] = np.nan  # 120 kHz silent: no measured cells
        ds["echo_range"][1, :, :] = np.nan
        _remove(ds, (0, 2, slice(None)), IMPULSE)
        assert self.kept(ds) == 3
