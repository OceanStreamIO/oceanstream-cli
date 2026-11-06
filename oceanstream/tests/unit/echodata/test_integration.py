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

        pruned = drop_noisy_pings(ds, drop_threshold=0.8, artefact_threshold=threshold)
        return pruned.sizes["ping_time"]

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


class TestIntegrationLayer:
    """Each channel is integrated from its start depth to where its noise reaches a limit."""

    @staticmethod
    def with_noise(ds, level_db=-150.0, absorption_db_m=1.0):
        """Noise per ping as echopype stores it; with 1 dB/m absorption it rises fast."""
        shape = (ds.sizes["channel"], ds.sizes["ping_time"])
        ds["background_noise"] = (("channel", "ping_time"), np.full(shape, level_db))
        ds["sound_absorption"] = (("channel", "ping_time"), np.full(shape, absorption_db_m))
        return ds

    def test_cells_above_the_start_depth_are_left_out(self):
        ds = _day()
        out = integration_view(ds, {38000: {"start_depth_m": 5.0}})
        sv, depth = out["Sv"].values[0, 0], out["depth"].values[0, 0]
        assert np.isnan(sv[:5]).all() and np.isnan(depth[:5]).all()
        assert (sv[5:] == -60.0).all()

    def test_integration_ends_where_the_noise_reaches_the_limit(self):
        ds = self.with_noise(_day())
        # Noise in Sv: -150 + 20 log10(r) + 2 r, above -120 dB from r = 7 m.
        out = integration_view(ds, {38000: {"max_noise_sv_db": -120.0}})
        sv = out["Sv"].values[0, 0]
        assert (sv[:7] == -60.0).all()
        assert np.isnan(sv[7:]).all()
        assert np.isnan(out["depth"].values[0, 0, 7:]).all()

    def test_cells_below_the_max_depth_are_left_out(self):
        # 18 kHz on EX2506 records past the 38 kHz range, so the seabed it sees there is
        # never masked, and its noise is too low for a noise limit to stop it in time.
        ds = _day()
        out = integration_view(ds, {38000: {"start_depth_m": 2.0, "max_depth_m": 12.0}})
        sv, depth = out["Sv"].values[0, 0], out["depth"].values[0, 0]
        assert np.isnan(sv[:2]).all() and (sv[2:13] == -60.0).all()
        assert np.isnan(sv[13:]).all() and np.isnan(depth[13:]).all()

    def test_a_channel_without_an_entry_is_not_limited(self):
        ds = self.with_noise(_day())
        out = integration_view(ds, {120000: {"start_depth_m": 5.0, "max_noise_sv_db": -120.0}})
        assert (out["Sv"].values == -60.0).all()
        assert out.attrs["integration_layers"] == "{}"

    def test_the_layer_is_recorded(self):
        import json

        out = integration_view(_day(), {38000: {"start_depth_m": 15.0}})
        assert json.loads(out.attrs["integration_layers"]) == {"38": {"start_depth_m": 15.0}}


class TestMvbsGapFill:
    @staticmethod
    def grid(values):
        """An MVBS-like Sv of one channel: rows are ping-time bins, columns depth bins."""
        values = np.asarray(values, dtype=float)[None]
        return xr.DataArray(values, dims=("channel", "ping_time", "depth"))

    @staticmethod
    def fill(sv, gaps, window):
        from oceanstream.echodata.compute.mvbs import fill_gaps

        mask = xr.DataArray(np.asarray(gaps, dtype=bool)[None], dims=sv.dims)
        out, filled = fill_gaps(sv, mask, window)
        return out.values[0], filled.values[0]

    def test_a_gap_gets_the_linear_mean_of_its_neighbours(self):
        sv = self.grid([[-60, -60, -60], [-60, np.nan, -70], [-60, -60, -60]])
        gaps = [[0, 0, 0], [0, 1, 0], [0, 0, 0]]
        out, filled = self.fill(sv, gaps, (3, 3))
        linear = (7 * 1e-6 + 1e-7) / 8
        assert out[1, 1] == pytest.approx(10 * np.log10(linear))
        assert filled[1, 1] and filled.sum() == 1

    def test_bins_below_the_noise_count_as_zero_and_stay(self):
        sv = self.grid([[-60, -999], [np.nan, -999], [-60, -999]])
        out, _ = self.fill(sv, [[0, 0], [1, 0], [0, 0]], (3, 3))
        assert out[1, 0] == pytest.approx(-60 + 10 * np.log10(2 / 5))  # 2 of 5 hold -60
        assert (out[:, 1] == -999).all()

    def test_a_gap_among_zeros_reads_as_no_backscatter(self):
        sv = self.grid([[-999], [np.nan], [-999]])
        out, _ = self.fill(sv, [[0], [1], [0]], (3, 1))
        assert out[1, 0] == -999

    def test_only_gaps_are_filled(self):
        sv = self.grid([[-60, np.nan], [-60, np.nan], [np.nan, np.nan]])
        # Column 1 is past the range; the last ping bin's gap has no neighbour with data
        # inside a one-bin-wide window.
        out, filled = self.fill(sv, [[0, 0], [0, 0], [1, 0]], (1, 1))
        assert np.isnan(out[:, 1]).all() and np.isnan(out[2, 0])
        assert not filled.any()

    def test_the_window_is_wider_than_it_is_tall(self):
        sv = self.grid([[-60, -80], [np.nan, -80], [-60, -80]])
        out, _ = self.fill(sv, [[0, 0], [1, 0], [0, 0]], (3, 1))
        assert out[1, 0] == pytest.approx(-60.0)  # the depth bin beside it is out of reach

    def test_the_window_must_be_odd(self):
        sv = self.grid([[-60]])
        with pytest.raises(ValueError, match="odd"):
            self.fill(sv, [[0]], (4, 3))

    def test_only_bins_emptied_by_artefacts_are_gaps(self):
        from oceanstream.echodata.compute import compute_mvbs
        from oceanstream.echodata.compute.mvbs import artefact_marker, fill_gaps

        ds = _day(n_pings=30, n_range=20)
        _remove(ds, (0, slice(10, 20), slice(5, 10)), IMPULSE)  # one whole 10 s x 5 m bin
        _remove(ds, (0, slice(None), slice(15, 20)), SEABED)
        view = integration_view(ds)

        mvbs = compute_mvbs(view, range_bin="5m", ping_time_bin="10s")["Sv"]
        held = compute_mvbs(artefact_marker(view), range_bin="5m", ping_time_bin="10s")["Sv"]
        gaps = mvbs.isnull() & held.notnull()
        assert int(gaps.sum()) == 1 and bool(gaps[0, 1, 1])

        out, filled = fill_gaps(mvbs, gaps, (3, 3))
        assert float(out[0, 1, 1]) == pytest.approx(-60.0)
        assert int(filled.sum()) == 1
