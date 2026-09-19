"""Unit coverage for :mod:`oceanstream.coastal.acquire.planetary`.

The screening measurement exists because tile-level cloud cover is the wrong
question, so the tests are about the measurement being right rather than about
the STAC plumbing. Two of them guard arithmetic that fails silently: the
baseline-4.0 radiometric offset (get it wrong and every post-2022 scene reads
0.1 too bright, which rejects the entire modern archive as hazy), and the
water mask (get it wrong and the median describes a beach).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")

from oceanstream.coastal.acquire.planetary import (  # noqa: E402
    BOA_ADD_OFFSET,
    MIN_WATER_PIXELS,
    WATER_NIR_MAX,
    DateVerdict,
    _classify,
    _coverage_fraction,
    _reflectance_offset,
    best_dates,
    measure_over_water_red,
    mgrs_tile,
    screen_dates,
)

# A small window off Sesimbra, in EPSG:4326 for test simplicity.
BBOX = (-9.24, 38.39, -9.15, 38.43)


class _FakeAsset:
    def __init__(self, href):
        self.href = href


class _FakeItem:
    def __init__(
        self, *, assets=None, properties=None, item_id="S2A_x_T29SMC_x", when=None, bbox=BBOX
    ):
        self.assets = assets or {}
        self.properties = properties or {}
        self.id = item_id
        self.datetime = when or datetime(2026, 6, 27, 11, 21, tzinfo=UTC)
        self.bbox = list(bbox) if bbox is not None else None


def _write_band(path, values):
    """A tiny EPSG:4326 raster covering BBOX exactly."""
    array = np.asarray(values, dtype=np.uint16)
    height, width = array.shape
    transform = rasterio.transform.from_bounds(*BBOX, width, height)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(array, 1)
    return str(path)


def _scene(tmp_path, *, red_dn, nir_dn=150, size=100, baseline="05.11"):
    """A fake item whose red/nir assets are real readable rasters.

    ``nir_dn`` defaults to 150 DN, i.e. reflectance 0.015 once the baseline
    offset is applied — plausible clear water, comfortably under the cut.
    ``size`` must survive ``decimate=4`` and still exceed MIN_WATER_PIXELS.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    red = np.full((size, size), red_dn, dtype=np.uint16)
    nir = np.full((size, size), nir_dn, dtype=np.uint16)
    return _FakeItem(
        assets={
            "B04": _FakeAsset(_write_band(tmp_path / "red.tif", red)),
            "B08": _FakeAsset(_write_band(tmp_path / "nir.tif", nir)),
        },
        properties={"s2:processing_baseline": baseline},
    )


class TestRadiometricOffset:
    """Baseline 4.0 (Jan 2022) added a -1000 DN offset. Ignoring it shifts
    every reflectance by +0.1, which is more than the entire clear-to-hazy
    range and would reject the whole post-2022 archive."""

    @pytest.mark.parametrize("baseline", ["04.00", "05.11", "05.09"])
    def test_modern_baselines_take_the_offset(self, baseline):
        item = _FakeItem(properties={"s2:processing_baseline": baseline})
        assert _reflectance_offset(item) == BOA_ADD_OFFSET

    @pytest.mark.parametrize("baseline", ["02.14", "03.01"])
    def test_legacy_baselines_do_not(self, baseline):
        item = _FakeItem(properties={"s2:processing_baseline": baseline})
        assert _reflectance_offset(item) == 0.0

    def test_unparseable_baseline_does_not_apply_a_guess(self):
        assert _reflectance_offset(_FakeItem(properties={})) == 0.0

    def test_offset_moves_a_scene_across_the_haze_threshold(self, tmp_path):
        """1300 DN is 0.03 with the offset and 0.13 without: clear vs hazy."""
        modern = _scene(tmp_path / "a", red_dn=1300, baseline="05.11")
        legacy = _scene(tmp_path / "b", red_dn=300, baseline="03.01")
        red_modern, _ = measure_over_water_red(modern, BBOX)
        red_legacy, _ = measure_over_water_red(legacy, BBOX)
        assert red_modern == pytest.approx(0.03, abs=1e-6)
        assert red_legacy == pytest.approx(0.03, abs=1e-6)


class TestMeasurement:
    def test_reads_uniform_water(self, tmp_path):
        scene = _scene(tmp_path, red_dn=1200)
        red, n_water = measure_over_water_red(scene, BBOX)
        assert red == pytest.approx(0.02, abs=1e-6)
        assert n_water >= MIN_WATER_PIXELS

    def test_an_all_water_window_keeps_every_pixel(self, tmp_path):
        """The quantile mask this replaced always discarded 60% of the window,
        keeping the darkest pixels and biasing the median toward 'clear'."""
        scene = _scene(tmp_path, red_dn=1200, size=100)
        _, n_water = measure_over_water_red(scene, BBOX)
        assert n_water == 25 * 25  # 100 / decimate=4, all of it

    def test_land_is_excluded_by_the_nir_cut(self, tmp_path):
        """Bright land in the window must not drag the water median upward."""
        size = 100
        red = np.full((size, size), 1200, dtype=np.uint16)
        nir = np.full((size, size), 150, dtype=np.uint16)
        # Top third is vegetated land: high NIR, and much brighter in red.
        red[: size // 3, :] = 4000
        nir[: size // 3, :] = 6000
        item = _FakeItem(
            assets={
                "B04": _FakeAsset(_write_band(tmp_path / "red.tif", red)),
                "B08": _FakeAsset(_write_band(tmp_path / "nir.tif", nir)),
            },
            properties={"s2:processing_baseline": "05.11"},
        )
        measured, n_water = measure_over_water_red(item, BBOX)
        assert measured == pytest.approx(0.02, abs=1e-6)
        assert n_water < 25 * 25

    def test_an_all_land_window_reports_no_water(self, tmp_path):
        """A relative mask would have called the darkest 40% of a forest 'water'."""
        scene = _scene(tmp_path, red_dn=3000, nir_dn=6000)
        red, n_water = measure_over_water_red(scene, BBOX)
        assert n_water == 0
        assert np.isnan(red)

    def test_turbid_water_can_be_admitted_by_raising_the_cut(self, tmp_path):
        """NIR 0.12 is a sediment-laden estuary, not land — the caller decides."""
        scene = _scene(tmp_path, red_dn=1600, nir_dn=2200)
        assert np.isnan(measure_over_water_red(scene, BBOX)[0])
        relaxed, n_water = measure_over_water_red(scene, BBOX, water_nir_max=0.15)
        assert relaxed == pytest.approx(0.06, abs=1e-6)
        assert n_water >= MIN_WATER_PIXELS

    def test_a_neighbouring_tile_that_misses_the_window_is_not_an_error(self, tmp_path):
        """Searching by AOI footprint routinely returns tiles that do not cover it."""
        scene = _scene(tmp_path, red_dn=1200)
        red, n_water = measure_over_water_red(scene, (10.0, 50.0, 10.1, 50.1))
        assert np.isnan(red)
        assert n_water == 0

    def test_a_partly_overlapping_window_reads_the_overlap(self, tmp_path):
        """Half in, half out is the AOI-straddles-a-tile-edge case."""
        scene = _scene(tmp_path, red_dn=1200)
        west, south, east, north = BBOX
        shifted = (west - (east - west) / 2, south, east - (east - west) / 2, north)
        red, n_water = measure_over_water_red(scene, shifted)
        assert red == pytest.approx(0.02, abs=1e-6)
        assert n_water > 0

    def test_default_cut_sits_between_water_and_vegetation(self):
        assert 0.05 < WATER_NIR_MAX < 0.15

    def test_too_little_water_reports_nan_rather_than_a_number(self, tmp_path):
        scene = _scene(tmp_path, red_dn=1200, size=20)
        red, n_water = measure_over_water_red(scene, BBOX)
        assert np.isnan(red)
        assert n_water < MIN_WATER_PIXELS


class TestNodataSentinel:
    """DN 0 is a gap, and the baseline-4.0 offset turns it into a measurement.

    This is the failure mode that ranks *best*, which is what makes it worth
    pinning down. Offsetting the sentinel yields exactly -0.1, below the NIR
    water cut, so an empty tile margin reads as wall-to-wall water with the
    darkest red in the search. Sentinel-2 carries wide nodata margins along UTM
    zone boundaries, and the STAC footprint is the tile's bounding rectangle
    rather than its valid-data extent, so coverage ranking cannot see it.
    """

    def test_an_all_nodata_window_reports_no_water(self, tmp_path):
        scene = _scene(tmp_path, red_dn=0, nir_dn=0, baseline="05.11")
        red, n_water = measure_over_water_red(scene, BBOX)
        assert n_water == 0
        assert np.isnan(red)

    def test_the_sentinel_is_not_mistaken_for_the_darkest_water(self, tmp_path):
        """Regression: it used to land on exactly -0.1 and win the ranking."""
        scene = _scene(tmp_path, red_dn=0, nir_dn=0, baseline="05.11")
        red, _ = measure_over_water_red(scene, BBOX)
        assert not (red == pytest.approx(-0.1, abs=1e-6))

    def test_a_nodata_margin_does_not_drag_the_median_down(self, tmp_path):
        """Half gap, half real water: the answer is the water, not the average."""
        size = 100
        red = np.full((size, size), 1200, dtype=np.uint16)
        nir = np.full((size, size), 150, dtype=np.uint16)
        red[: size // 2, :] = 0
        nir[: size // 2, :] = 0
        item = _FakeItem(
            assets={
                "B04": _FakeAsset(_write_band(tmp_path / "red.tif", red)),
                "B08": _FakeAsset(_write_band(tmp_path / "nir.tif", nir)),
            },
            properties={"s2:processing_baseline": "05.11"},
        )
        measured, n_water = measure_over_water_red(item, BBOX)
        assert measured == pytest.approx(0.02, abs=1e-6)
        assert n_water == 25 * 13  # only the observed half, decimated

    def test_real_negative_reflectance_is_kept(self, tmp_path):
        """Atmospheric correction over dark water legitimately overshoots below
        zero. Clamping that away to fix the sentinel would bias the median up."""
        scene = _scene(tmp_path, red_dn=950, nir_dn=800, baseline="05.11")
        red, n_water = measure_over_water_red(scene, BBOX)
        assert red == pytest.approx(-0.005, abs=1e-6)
        assert n_water >= MIN_WATER_PIXELS

    def test_legacy_scenes_without_the_offset_also_drop_the_sentinel(self, tmp_path):
        """Pre-baseline-4.0 the sentinel reads as 0.0, which is dark but not
        impossible — still a gap, and still not something to take a median of."""
        scene = _scene(tmp_path, red_dn=0, nir_dn=0, baseline="03.01")
        red, n_water = measure_over_water_red(scene, BBOX)
        assert n_water == 0
        assert np.isnan(red)


class TestClassification:
    @pytest.mark.parametrize(
        ("red", "expected"),
        [(0.01, "clear"), (0.03, "clear"), (0.045, "marginal"), (0.06, "hazy"), (0.2, "hazy")],
    )
    def test_thresholds(self, red, expected):
        assert _classify(red, 1000, 0.03, 0.06) == expected

    def test_no_water_outranks_the_reflectance_value(self):
        assert _classify(0.01, 10, 0.03, 0.06) == "no_water"

    def test_nan_is_unreadable_not_clear(self):
        assert _classify(float("nan"), 1000, 0.03, 0.06) == "unreadable"

    def test_thresholds_are_arguments_because_water_types_differ(self):
        """0.045 over a turbid Irish AOI is the water, not the atmosphere."""
        assert _classify(0.045, 1000, 0.03, 0.06) == "marginal"
        assert _classify(0.045, 1000, 0.05, 0.10) == "clear"


class TestCoverageRanking:
    """A coastal AOI on a UTM zone boundary comes back from both neighbouring
    tiles. Ranking those by cloud alone picks on a criterion unrelated to
    whether the scene contains the site — observed live at the Summer Isles,
    where the clearer T29VPE covers ~4% of the window that T30VUK covers whole."""

    def test_full_overlap_is_one(self):
        assert _coverage_fraction(_FakeItem(bbox=BBOX), BBOX) == pytest.approx(1.0)

    def test_disjoint_footprint_is_zero(self):
        assert _coverage_fraction(_FakeItem(bbox=(10, 50, 11, 51)), BBOX) == 0.0

    def test_half_overlap_is_a_half(self):
        west, south, east, north = BBOX
        half = (west, south, (west + east) / 2, north)
        assert _coverage_fraction(_FakeItem(bbox=half), BBOX) == pytest.approx(0.5)

    def test_missing_footprint_does_not_penalise_the_item(self):
        """Unknown coverage must not lose to a known sliver; let cloud decide."""
        assert _coverage_fraction(_FakeItem(bbox=None), BBOX) == 1.0


class TestVerdicts:
    def test_only_clear_and_marginal_are_worth_the_download(self):
        for verdict, expected in [
            ("clear", True),
            ("marginal", True),
            ("hazy", False),
            ("tile_cloudy", False),
            ("no_water", False),
            ("unreadable", False),
        ]:
            row = DateVerdict(
                datetime(2026, 6, 1).date(), "S2A", "T29SMC", 10.0, 0.02, 900, verdict
            )
            assert row.worth_downloading is expected

    def test_best_dates_ranks_by_measured_red_not_tile_cloud(self):
        rows = [
            DateVerdict(datetime(2026, 6, 1).date(), "S2A", "T1", 5.0, 0.028, 900, "clear"),
            DateVerdict(datetime(2026, 6, 6).date(), "S2B", "T1", 55.0, 0.011, 900, "clear"),
            DateVerdict(datetime(2026, 6, 9).date(), "S2A", "T1", 1.0, 0.09, 900, "hazy"),
        ]
        picked = best_dates(rows, n=2)
        assert [r.date.day for r in picked] == [6, 1]

    def test_serialisation_keeps_the_measurement_next_to_the_verdict(self):
        row = DateVerdict(datetime(2026, 6, 1).date(), "S2A", "T29SMC", 12.0, 0.021, 900, "clear")
        payload = row.to_dict()
        assert payload["date"] == "2026-06-01"
        assert payload["over_water_red"] == 0.021
        assert payload["verdict"] == "clear"


class TestSparseSceneRanking:
    """A median over a handful of pixels is a hole in the cloud, not the site.

    Those gaps are cloud-shadowed, so they read *dark* and therefore rank
    first: the least observed scene wins. `MIN_WATER_PIXELS` cannot catch it,
    being an absolute floor with no idea how much water the window holds when
    the view is clear. Screening a date range reveals that number, so the
    best-observed date in the set supplies the reference.
    """

    @staticmethod
    def _row(day, red, n_water):
        return DateVerdict(
            datetime(2026, 6, day).date(), "S2A", "T29SMC", 5.0, red, n_water, "clear"
        )

    def test_a_sparse_dark_scene_does_not_outrank_a_full_one(self):
        """Regression: 204 water pixels beat 31,424 on a ~31,500-pixel window.

        The fragment is dropped rather than demoted. It is not a worse
        candidate for download, it is not a candidate: there is no site in it.
        """
        rows = [
            self._row(1, 0.0060, 31424),
            self._row(6, -0.0086, 204),
        ]
        assert [r.date.day for r in best_dates(rows, n=2)] == [1]

    def test_the_reference_is_the_best_observed_date_not_a_constant(self):
        """Same 204-pixel scene, but now every date is that sparse: it ranks."""
        rows = [self._row(1, 0.0060, 210), self._row(6, -0.0086, 204)]
        assert best_dates(rows, n=1)[0].date.day == 6

    def test_a_degenerate_set_still_returns_an_answer(self):
        rows = [self._row(1, 0.02, 0), self._row(6, 0.01, 0)]
        assert len(best_dates(rows, n=2)) == 2

    def test_partial_coverage_above_the_floor_is_still_ranked_on_clarity(self):
        """The floor screens out fragments, it does not demand a perfect scene."""
        rows = [
            self._row(1, 0.0060, 30000),
            self._row(6, 0.0020, 18000),  # 60% observed, and clearly clearer
        ]
        assert best_dates(rows, n=1)[0].date.day == 6

    def test_the_floor_can_be_disabled(self):
        rows = [self._row(1, 0.0060, 31424), self._row(6, -0.0086, 204)]
        picked = best_dates(rows, n=1, min_coverage_fraction=0.0)
        assert picked[0].date.day == 6


class TestTileIdentification:
    def test_prefers_the_stac_property(self):
        item = _FakeItem(properties={"s2:mgrs_tile": "29SMC"})
        assert mgrs_tile(item) == "T29SMC"

    def test_falls_back_to_the_item_id(self):
        item = _FakeItem(item_id="S2A_MSIL2A_20260601T112121_R037_T30UVF_20260601T140012")
        assert mgrs_tile(item) == "T30UVF"

    def test_returns_none_when_neither_is_present(self):
        assert mgrs_tile(_FakeItem(item_id="no-tile-here")) is None


class TestScreeningLoop:
    def _patch_catalog(self, monkeypatch, items):
        class _Search:
            def items(self_inner):
                return iter(items)

        class _Catalog:
            def search(self_inner, **kwargs):
                self_inner.kwargs = kwargs
                return _Search()

        catalog = _Catalog()
        monkeypatch.setattr(
            "oceanstream.coastal.acquire.planetary.open_catalog", lambda: catalog
        )
        return catalog

    def test_cloudy_tiles_are_recorded_not_dropped(self, monkeypatch):
        """A cloudy tile can still be clear over the AOI, so the date stays visible."""
        item = _FakeItem(properties={"eo:cloud_cover": 88.0, "platform": "sentinel-2a"})
        self._patch_catalog(monkeypatch, [item])
        rows = screen_dates(BBOX, "2026-06-01", "2026-06-30", max_tile_cloud_pct=60.0)
        assert [r.verdict for r in rows] == ["tile_cloudy"]
        assert rows[0].tile_cloud_pct == 88.0
    def test_raising_the_cloud_limit_reaches_the_measurement(self, monkeypatch, tmp_path):
        scene = _scene(tmp_path, red_dn=1200)
        scene.properties["eo:cloud_cover"] = 88.0
        self._patch_catalog(monkeypatch, [scene])
        rows = screen_dates(BBOX, "2026-06-01", "2026-06-30", max_tile_cloud_pct=95.0)
        assert rows[0].verdict == "clear"

    def test_one_row_per_date_keeping_the_clearest_item(self, monkeypatch, tmp_path):
        when = datetime(2026, 6, 27, 11, 21, tzinfo=UTC)
        cloudy = _FakeItem(properties={"eo:cloud_cover": 90.0}, when=when)
        clear = _scene(tmp_path, red_dn=1200)
        clear.properties["eo:cloud_cover"] = 3.0
        clear.datetime = when
        self._patch_catalog(monkeypatch, [cloudy, clear])
        rows = screen_dates(BBOX, "2026-06-01", "2026-06-30")
        assert len(rows) == 1
        assert rows[0].verdict == "clear"

    def test_a_clearer_tile_that_barely_covers_the_aoi_does_not_win(
        self, monkeypatch, tmp_path
    ):
        """The Summer Isles zone-boundary case, reproduced."""
        when = datetime(2026, 6, 27, 11, 21, tzinfo=UTC)
        west, south, east, north = BBOX
        sliver = _FakeItem(
            properties={"eo:cloud_cover": 3.0, "s2:mgrs_tile": "29VPE"},
            when=when,
            bbox=(west, south, west + (east - west) * 0.05, north),
        )
        covering = _scene(tmp_path, red_dn=1200)
        covering.properties.update({"eo:cloud_cover": 50.0, "s2:mgrs_tile": "30VUK"})
        covering.datetime = when
        self._patch_catalog(monkeypatch, [sliver, covering])
        rows = screen_dates(BBOX, "2026-06-01", "2026-06-30", max_tile_cloud_pct=70.0)
        assert len(rows) == 1
        assert rows[0].tile == "T30VUK"
        assert rows[0].verdict == "clear"

    def test_an_unreadable_scene_does_not_abandon_the_range(self, monkeypatch, tmp_path):
        broken = _FakeItem(
            assets={"B04": _FakeAsset("/nonexistent/red.tif"), "B08": _FakeAsset("/nope.tif")},
            properties={"eo:cloud_cover": 5.0, "s2:processing_baseline": "05.11"},
            when=datetime(2026, 6, 1, tzinfo=UTC),
        )
        good = _scene(tmp_path, red_dn=1200)
        good.properties["eo:cloud_cover"] = 5.0
        good.datetime = datetime(2026, 6, 6, tzinfo=UTC)
        self._patch_catalog(monkeypatch, [broken, good])
        rows = screen_dates(BBOX, "2026-06-01", "2026-06-30")
        assert [r.verdict for r in rows] == ["unreadable", "clear"]
