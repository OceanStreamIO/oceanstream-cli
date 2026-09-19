"""Integration tests for the split-source denoise comparison plumbing.

Exercises stage-5 resume against a small two-category / 38 + 200 kHz fixture:

* the source container is read but never written to (fingerprint gate);
* products land only in the output container;
* strict mode aborts on an injected filter failure;
* a split-container run reproduces a same-container run within the tolerances
  declared in ``experiment_contract``.

The last item replaces the invalid "compare against the historical v1 products"
oracle: those products are unprovenanced, so the only sound regression target is
the split-vs-same-container equivalence of a run we control.
"""

from __future__ import annotations

from oceanstream.echodata.products import open_product_uri
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

pytestmark = pytest.mark.integration

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "batch_processing"

#: ``scripts/batch_processing/config.py`` shadows the ``oceanstream/config``
#: package; leaving it cached breaks unrelated settings tests in the same session.
_SHADOWED_MODULES = ("config",)


@contextmanager
def scripts_importable():
    """Import the batch scripts without leaking sys.path, sys.modules or env."""
    env_snapshot = dict(os.environ)
    path_snapshot = list(sys.path)
    shadowed = {name: sys.modules.get(name) for name in _SHADOWED_MODULES}
    for name in _SHADOWED_MODULES:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(_SCRIPTS))
    try:
        yield
    finally:
        sys.path[:] = path_snapshot
        for name, module in shadowed.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        os.environ.clear()
        os.environ.update(env_snapshot)

DAY = "2023-10-10"
CATEGORIES = ("long_pulse", "short_pulse")
SOURCE_CONTAINER = "fixture-source"


def _sv_fixture(n_pings: int, freqs: list[float], seed: int) -> xr.Dataset:
    """Small but structurally faithful stage-4 Sv dataset."""
    rng = np.random.default_rng(seed)
    ping_time = pd.date_range("2023-10-10T02:00:00", periods=n_pings, freq="3s")
    n_range = 120
    range_sample = np.arange(n_range)
    depth = 1.9 + range_sample * 2.0

    sv = np.empty((len(freqs), n_pings, n_range), dtype=np.float32)
    for c, _freq in enumerate(freqs):
        base = -95.0 + 25.0 * np.exp(-((depth - 90.0) ** 2) / (2 * 25.0**2))
        sv[c] = base[None, :] + rng.normal(0.0, 1.5, size=(n_pings, n_range))
        sv[c, ::37, :] += 25.0  # periodic impulse spikes

    lat = np.linspace(0.010, 0.028, n_pings)
    lon = np.linspace(-166.40, -166.15, n_pings)

    depth_3d = np.broadcast_to(depth, (len(freqs), n_pings, n_range)).copy()
    return xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "range_sample"], sv),
            "depth": (["channel", "ping_time", "range_sample"], depth_3d),
            "latitude": ("ping_time", lat),
            "longitude": ("ping_time", lon),
            "frequency_nominal": ("channel", freqs),
        },
        coords={
            "channel": [f"ch{int(f / 1000)}" for f in freqs],
            "ping_time": ping_time,
            "range_sample": range_sample,
        },
        attrs={"processing_level": "Level 2A"},
    )


@pytest.fixture
def experiment_root(tmp_path):
    """Local storage root holding an immutable two-category Sv source."""
    with scripts_importable():
        import local_storage

    root = tmp_path / "experiment"
    local_storage.patch_storage(root)

    source_dir = root / SOURCE_CONTAINER / DAY
    source_dir.mkdir(parents=True)
    _sv_fixture(120, [38000.0], seed=1).to_zarr(
        source_dir / f"{DAY}--long_pulse.zarr", mode="w"
    )
    _sv_fixture(240, [38000.0, 200000.0], seed=2).to_zarr(
        source_dir / f"{DAY}--short_pulse.zarr", mode="w"
    )
    return root


def _config(
    output_container: str,
    *,
    sv_source: str | None,
    strict: bool = False,
    force: bool = False,
):
    with scripts_importable():
        from config import DaskConfig, PipelineConfig
        from experiment_contract import PRESETS_BY_KEY

    cfg = PipelineConfig()
    cfg.cruise_id = "FIXTURE"
    cfg.start_date = datetime.fromisoformat(DAY)
    cfg.end_date = datetime.fromisoformat(DAY)
    cfg.output_container = output_container
    cfg.sv_source_container = sv_source or ""
    cfg.resume_stage = 5
    cfg.stop_after_stage = 9
    cfg.strict = strict
    cfg.force = force
    cfg.expected_categories = list(CATEGORIES)
    cfg.parallel_workers = 1
    cfg.dask = DaskConfig(n_workers=1, memory_limit="2GB")
    cfg.skip_mvbs = True
    cfg.skip_nasc = True
    cfg.skip_echograms = True
    cfg.skip_pmtiles = True
    cfg.build_campaign_zarr = False
    cfg.emit_denoise_diagnostics = True
    cfg.preset_key = "tpv3"
    cfg.preset_toml = str(PRESETS_BY_KEY["tpv3"].toml_path)
    cfg.denoise.methods = ["impulse"]
    cfg.denoise.use_frequency_specific = False
    cfg.denoise.sv_clip_max_db = -10.0
    cfg.prune.crosstalk_enabled = False
    return cfg


def _fingerprints(root: Path) -> dict:
    with scripts_importable():
        from experiment_contract import fingerprint_tree

    return {
        category: fingerprint_tree(
            root / SOURCE_CONTAINER / DAY / f"{DAY}--{category}.zarr"
        )["sha256"]
        for category in CATEGORIES
    }


def _run(cfg):
    with scripts_importable():
        import process_from_raw

        process_from_raw.run_pipeline(cfg)


class TestSplitSourceRun:
    def test_source_container_is_not_modified(self, experiment_root):
        before = _fingerprints(experiment_root)
        _run(_config("out-split", sv_source=SOURCE_CONTAINER))
        assert _fingerprints(experiment_root) == before

    def test_products_land_only_in_the_output_container(self, experiment_root):
        _run(_config("out-split", sv_source=SOURCE_CONTAINER))

        out_day = experiment_root / "out-split" / DAY
        source_day = experiment_root / SOURCE_CONTAINER / DAY

        for category in CATEGORIES:
            assert (out_day / f"{DAY}--{category}--denoised.zarr").exists()
            assert (out_day / f"{DAY}--{category}--pruned.zarr").exists()
            assert (out_day / f"{DAY}--{category}--denoise_stats.json").exists()
            assert not (source_day / f"{DAY}--{category}--denoised.zarr").exists()

        assert sorted(p.name for p in source_day.iterdir()) == [
            f"{DAY}--{category}.zarr" for category in CATEGORIES
        ]

    def test_denoised_and_pruned_store_no_sv_of_their_own(self, experiment_root):
        from oceanstream.echodata import products

        _run(_config("out-split", sv_source=SOURCE_CONTAINER))
        for category in CATEGORIES:
            day = experiment_root / "out-split" / DAY
            denoised = xr.open_zarr(day / f"{DAY}--{category}--denoised.zarr")
            pruned = xr.open_zarr(day / f"{DAY}--{category}--pruned.zarr")
            try:
                assert "Sv" not in denoised
                assert denoised.attrs[products.PRODUCT_ATTR] == products.MASKED_SV
                assert denoised.attrs["sv_source_container"] == SOURCE_CONTAINER
                assert denoised["denoise_flags"].dtype == np.dtype("uint8")
                impulse = products.FLAG_BITS["impulse"]
                assert bool(((denoised["denoise_flags"] & impulse) != 0).any())
                assert "Sv" not in pruned
                assert pruned.attrs[products.PRODUCT_ATTR] == products.PRUNED_VIEW
            finally:
                denoised.close()
                pruned.close()

            # Opened through the resolver they are ordinary Sv datasets again.
            rebuilt = products.open_product_uri(str(day / f"{DAY}--{category}--pruned.zarr"))
            assert "Sv" in rebuilt and "latitude" in rebuilt
            assert rebuilt.sizes["ping_time"] <= denoised.sizes["ping_time"]

    def test_stats_json_is_strict_and_complete(self, experiment_root):
        import json

        with scripts_importable():
            from experiment_contract import DENOISE_STATS_SCHEMA

        _run(_config("out-split", sv_source=SOURCE_CONTAINER))
        path = (
            experiment_root / "out-split" / DAY / f"{DAY}--short_pulse--denoise_stats.json"
        )
        text = path.read_text()
        assert "NaN" not in text
        payload = json.loads(text)

        assert payload["schema"] == DENOISE_STATS_SCHEMA
        assert payload["preset"] == "tpv3"
        assert payload["ping_counts"]["after_nan_prune"] is not None
        assert len(payload["channels"]) == 2
        for channel in payload["channels"]:
            masks = channel["masks"]
            assert masks["denominator"] > 0
            assert "impulse" in masks["per_mask"]
            assert channel["mask_union_identity"]["status"] == "ok"
            assert channel["sanity_clip"]["threshold_db"] == -10.0

    def test_run_manifest_written_only_on_full_validation(self, experiment_root):
        # MVBS/NASC are skipped, so the artifact matrix is incomplete and the
        # certificate must be withheld.
        _run(_config("out-split", sv_source=SOURCE_CONTAINER))
        assert not (experiment_root / "out-split" / "run-manifest.json").exists()


class TestSplitVsSameContainer:
    def test_denoised_sv_matches_within_declared_tolerance(self, experiment_root):
        import shutil

        with scripts_importable():
            from experiment_contract import TOLERANCES

        _run(_config("out-split", sv_source=SOURCE_CONTAINER))

        # Same-container arm: copy the source in and resume in place. The
        # container is populated by design, so --force is required.
        same = experiment_root / "out-same"
        shutil.copytree(experiment_root / SOURCE_CONTAINER, same)
        _run(_config("out-same", sv_source=None, force=True))

        for category in CATEGORIES:
            a = open_product_uri(
                str(experiment_root / "out-split" / DAY / f"{DAY}--{category}--denoised.zarr")
            )
            b = open_product_uri(str(same / DAY / f"{DAY}--{category}--denoised.zarr"))
            try:
                assert a["Sv"].shape == b["Sv"].shape
                va = a["Sv"].values
                vb = b["Sv"].values
                # equal_nan=False: a NaN opposite a finite value is a difference.
                assert np.array_equal(np.isfinite(va), np.isfinite(vb))
                finite = np.isfinite(va)
                assert np.allclose(
                    va[finite], vb[finite], atol=TOLERANCES.sv_atol_db, rtol=0
                )
                np.testing.assert_allclose(
                    a["latitude"].values, b["latitude"].values,
                    atol=TOLERANCES.coord_atol_deg,
                )
            finally:
                a.close()
                b.close()


class TestStrictMode:
    def test_injected_filter_failure_aborts(self, experiment_root, monkeypatch):
        with scripts_importable():
            import process_campaign

        def _boom(_ds, _params):
            raise RuntimeError("injected filter failure")

        monkeypatch.setattr(
            "oceanstream.echodata.denoise.impulse_noise.impulse_noise_mask", _boom
        )
        with pytest.raises(
            (process_campaign.StrictModeError, RuntimeError),
            match="injected filter failure|Denoise",
        ):
            _run(_config("out-strict", sv_source=SOURCE_CONTAINER, strict=True))

    def test_missing_expected_category_aborts(self, experiment_root):
        (
            experiment_root / SOURCE_CONTAINER / DAY / f"{DAY}--long_pulse.zarr"
        ).rename(experiment_root / SOURCE_CONTAINER / DAY / "stashed.zarr")
        with pytest.raises(RuntimeError, match="Missing expected"):
            _run(_config("out-missing", sv_source=SOURCE_CONTAINER, strict=True))

    def test_non_empty_output_container_is_refused(self, experiment_root):
        stale = experiment_root / "out-dirty" / DAY
        stale.mkdir(parents=True)
        (stale / "leftover.txt").write_text("from a previous run")
        with pytest.raises(RuntimeError, match="not empty"):
            _run(_config("out-dirty", sv_source=SOURCE_CONTAINER))

    def test_source_may_not_be_the_output(self, experiment_root):
        with pytest.raises(ValueError, match="must differ"):
            _run(_config(SOURCE_CONTAINER, sv_source=SOURCE_CONTAINER))
