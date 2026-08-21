"""End-to-end smoke test for the denoise comparison report generator.

Builds three synthetic preset runs with the full artifact matrix, then runs
``generate_denoise_report`` over them. This exercises metrics assembly, both
comparison families, every figure, and the markdown build — the PDF step is
covered separately because it needs pandoc + xelatex on the host.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")
pytest.importorskip("matplotlib")

pytestmark = pytest.mark.integration

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "batch_processing"
_SHADOWED_MODULES = ("config",)

DAY = "2023-10-10"
CATEGORIES = ("long_pulse", "short_pulse")
PRESETS = ("ryan-inspired", "tpv1", "tpv3")
FREQS = {"long_pulse": [38000.0], "short_pulse": [38000.0, 200000.0]}


@contextmanager
def scripts_importable():
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


with scripts_importable():
    import experiment_contract as contract  # noqa: E402
    import generate_denoise_report as report  # noqa: E402


def _channels(freqs):
    return [f"ch{int(f / 1000)}" for f in freqs]


def _sv_dataset(n_pings, freqs, *, offset_db=0.0, nan_fraction=0.0, seed=0, depth_3d=False):
    """Synthetic Sv.

    ``depth_3d`` reproduces the real stage-4 layout, where ``depth`` is a
    ``(channel, ping_time, range_sample)`` **data variable** rather than a 1-D
    coordinate. Both layouts occur in the experiment and the report has to read
    either; a fixture that only had the 1-D form once let a silent
    figure-skipping bug through.
    """
    rng = np.random.default_rng(seed)
    ping_time = pd.date_range("2023-10-10T02:00:00", periods=n_pings, freq="10s")
    depth = np.arange(0.0, 300.0, 10.0)
    sv = np.empty((len(freqs), n_pings, depth.size))
    for c in range(len(freqs)):
        profile = -95.0 + 25.0 * np.exp(-((depth - 90.0) ** 2) / (2 * 30.0**2))
        sv[c] = profile[None, :] + offset_db + rng.normal(0.0, 0.5, (n_pings, depth.size))
    if nan_fraction:
        holes = rng.random(sv.shape) < nan_fraction
        sv[holes] = np.nan

    if not depth_3d:
        return xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "depth"], sv),
                "frequency_nominal": ("channel", freqs),
            },
            coords={"channel": _channels(freqs), "ping_time": ping_time, "depth": depth},
        )

    depth_cube = np.broadcast_to(depth, sv.shape).copy()
    depth_cube[:, ::17, :] = np.nan  # per-ping NaNs, as in the real product
    return xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "range_sample"], sv),
            "depth": (["channel", "ping_time", "range_sample"], depth_cube),
            "frequency_nominal": ("channel", freqs),
        },
        coords={
            "channel": _channels(freqs),
            "ping_time": ping_time,
            "range_sample": np.arange(depth.size),
        },
    )


def _nasc_dataset(freqs, *, scale=1.0, n_bins=6):
    depth = np.arange(0.0, 300.0, 10.0)
    distance = np.arange(n_bins, dtype=float) * 0.5
    values = np.empty((len(freqs), n_bins, depth.size))
    for c in range(len(freqs)):
        values[c] = scale * (5.0 + np.arange(n_bins)[:, None] + depth[None, :] * 0.01)
    ds = xr.Dataset(
        {
            "NASC": (["channel", "distance", "depth"], values),
            "latitude": ("distance", np.linspace(0.010, 0.026, n_bins)),
            "longitude": ("distance", np.linspace(-166.40, -166.20, n_bins)),
            "ping_time": (
                "distance",
                pd.date_range("2023-10-10T02:00:00", periods=n_bins, freq="20min"),
            ),
        },
        coords={
            "channel": _channels(freqs),
            "distance": distance,
            "depth": depth,
            "frequency_nominal": ("channel", freqs),
        },
    )
    ds["NASC"].attrs["units"] = "m2 nmi-2"
    ds["distance"].attrs["units"] = "nmi"
    return ds


def _stats_payload(preset, category, freqs):
    channels = []
    for freq in freqs:
        channels.append({
            "channel": f"ch{int(freq / 1000)}",
            "frequency_hz": freq,
            "depth_band_m": list(contract.TOLERANCES.integration_bands_m[str(int(freq))]),
            "cells_total": 6000,
            "cells_in_band": 4000,
            "source_finite": 3800,
            "source_nan_in_band": 200,
            "filter_status": {"impulse": "ran", "transient": "ran"},
            "masks": {
                "denominator": 3800,
                "denominator_definition": "source-finite Sv cells in band",
                "per_mask": {
                    "impulse": {"n_flagged": 120, "fraction_of_valid": 120 / 3800},
                    "transient": {"n_flagged": 60, "fraction_of_valid": 60 / 3800},
                },
                "unique_contribution": {
                    "impulse": {"n_flagged": 100, "n_unique": 100, "fraction_of_valid": 100 / 3800},
                    "transient": {"n_flagged": 40, "n_unique": 40, "fraction_of_valid": 40 / 3800},
                },
                "pairwise_overlap": {
                    "impulse|transient": {"n_overlap": 20, "fraction_of_valid": 20 / 3800}
                },
                "union": {"n_flagged": 160, "fraction_of_valid": 160 / 3800},
            },
            "background_correction": {
                "kind": "sv_transform",
                "n_finite_before": 3800,
                "n_finite_after": 3700,
                "n_finite_to_nan": 100,
                "fraction_finite_to_nan": 100 / 3800,
                "sv_change_db": {
                    "status": "ok", "n": 3700, "mean": -0.4, "median": -0.35,
                    "p5": -1.2, "p95": 0.0, "min": -3.0, "max": 0.0,
                },
            },
            "sanity_clip": {
                "threshold_db": -10.0, "n_newly_clipped": 3,
                "fraction_of_valid": 3 / 3800,
            },
            "sv_percentiles": {"source": {"status": "ok", "n_finite": 3800}},
            "mask_union_identity": {"status": "ok", "n_mismatch": 0},
        })
    return {
        "schema": contract.DENOISE_STATS_SCHEMA,
        "day": DAY,
        "category": category,
        "preset": preset,
        "ping_counts": {
            "source": 200, "before_prune": 200,
            "after_nan_prune": 190, "after_crosstalk_prune": 185,
        },
        "resolved_params": {
            f"ch{int(freq / 1000)}": {
                "frequency_hz": freq,
                "methods": {
                    "impulse": {
                        "status": "resolved",
                        "params": {"threshold_db": 10.0, "ping_lags": [1, 2]},
                    },
                    "transient": {
                        "status": "resolved",
                        "params": {"thr_dB": 12.0, "n_pings": 50},
                    },
                },
            }
            for freq in freqs
        },
        "unused_toml_fields": [
            {
                "frequency_hz": "38000", "method": "transient", "key": "percentile",
                "reason": "Ryan-TN-only parameter; the wired detector ignores it",
            }
        ],
        "tolerances": contract.TOLERANCES.to_dict(),
        "channels": channels,
    }


@pytest.fixture
def synthetic_experiment(tmp_path):
    """Three complete preset runs plus the frozen experiment manifest."""
    root = tmp_path / "experiment"
    source_root = root / "fixture-source"
    runs_dir = tmp_path / "runs"

    for i, category in enumerate(CATEGORIES):
        freqs = FREQS[category]
        day_dir = source_root / DAY
        day_dir.mkdir(parents=True, exist_ok=True)
        _sv_dataset(60, freqs, seed=i, depth_3d=True).to_zarr(
            day_dir / f"{DAY}--{category}.zarr", mode="w"
        )

    for p_i, preset in enumerate(PRESETS):
        container = root / f"out-{preset}"
        day_dir = container / DAY
        day_dir.mkdir(parents=True, exist_ok=True)
        for c_i, category in enumerate(CATEGORIES):
            freqs = FREQS[category]
            seed = 100 * p_i + c_i
            offset = 0.0 if preset == "ryan-inspired" else 0.4 * p_i

            # Sv-domain products keep the 3-D depth variable; MVBS/NASC carry a
            # 1-D depth coordinate, exactly as the pipeline produces them.
            _sv_dataset(60, freqs, offset_db=offset, nan_fraction=0.05 * p_i,
                        seed=seed, depth_3d=True).to_zarr(
                day_dir / f"{DAY}--{category}--denoised.zarr", mode="w"
            )
            _sv_dataset(58 - p_i, freqs, offset_db=offset, nan_fraction=0.05 * p_i,
                        seed=seed, depth_3d=True).to_zarr(
                day_dir / f"{DAY}--{category}--pruned.zarr", mode="w"
            )
            _sv_dataset(58 - p_i, freqs, offset_db=offset, seed=seed).to_zarr(
                day_dir / f"{DAY}--{category}--mvbs.zarr", mode="w"
            )
            _nasc_dataset(freqs, scale=1.0 + 0.05 * p_i, n_bins=6 - p_i).to_zarr(
                day_dir / f"{DAY}--{category}--nasc.zarr", mode="w"
            )
            (day_dir / f"{DAY}--{category}--masks.zarr").mkdir(exist_ok=True)
            contract.write_json_atomic(
                day_dir / f"{DAY}--{category}--denoise_stats.json",
                _stats_payload(preset, category, freqs),
            )

        manifest = {
            "schema": contract.RUN_MANIFEST_SCHEMA,
            "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "preset": preset,
            "preset_toml": f"{preset}.toml",
            "preset_toml_sha256": "0" * 64,
            "output_container": f"out-{preset}",
            "sv_source_container": "fixture-source",
            "days": [DAY],
            "categories": list(CATEGORIES),
            "code": {"head": "abc123def456", "dirty": False, "dirty_files": {}},
            "environment": {"python": "3.12.12", "platform": "test", "packages": {}},
        }
        contract.write_json_atomic(container / "run-manifest.json", manifest)
        contract.write_json_atomic(runs_dir / preset / "run-manifest.json", manifest)

    experiment_manifest = {
        "schema": contract.EXPERIMENT_MANIFEST_SCHEMA,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "day": DAY,
        "source_root": str(source_root),
        "source_container": "fixture-source",
        "categories": list(CATEGORIES),
        "sources": {
            category: {
                "path": str(source_root / DAY / f"{DAY}--{category}.zarr"),
                "sha256": f"{i}" * 64,
                "n_files": 10,
                "n_bytes": 1024,
                "description": {
                    "n_pings": 60,
                    "acquisition_start_utc": "2023-10-10T02:00:00",
                    "acquisition_end_utc": "2023-10-10T02:10:00",
                    "frequencies_hz": FREQS[category],
                    "gps": {
                        "latitude": {"min": 0.010, "max": 0.026, "n": 60, "n_finite": 60},
                        "longitude": {"min": -166.40, "max": -166.20, "n": 60, "n_finite": 60},
                    },
                },
            }
            for i, category in enumerate(CATEGORIES)
        },
        "tolerances": contract.TOLERANCES.to_dict(),
        "presets": [{"key": p, "label": p, "toml": f"{p}.toml",
                     "toml_sha256": "0" * 64, "container": f"out-{p}",
                     "lineage": "synthetic"} for p in PRESETS],
        "baseline_preset": contract.BASELINE_PRESET,
        "code": {"head": "abc123def456", "dirty": False, "dirty_files": {}},
        "environment": {"python": "3.12.12", "platform": "test", "packages": {}},
    }
    manifest_path = tmp_path / "experiment-manifest.json"
    contract.write_json_atomic(manifest_path, experiment_manifest)

    return {
        "manifest": manifest_path,
        "root": root,
        "runs_dir": runs_dir,
        "out_dir": tmp_path / "comparison",
    }


def _run_report(fixture, extra: list[str] | None = None) -> int:
    with scripts_importable():
        return report.main([
            "--experiment-manifest", str(fixture["manifest"]),
            "--experiment-root", str(fixture["root"]),
            "--runs-dir", str(fixture["runs_dir"]),
            "--out-dir", str(fixture["out_dir"]),
            "--day", DAY,
            "--skip-pdf",
            *(extra or []),
        ])


class TestReportSmoke:
    def test_report_builds(self, synthetic_experiment):
        assert _run_report(synthetic_experiment) == 0
        out = synthetic_experiment["out_dir"]
        assert (out / "metrics.json").is_file()
        assert (out / "results.md").is_file()
        assert (out / "references.bib").is_file()
        assert any((out / "figures").glob("*.png"))

    def test_html_is_produced_without_any_tex_engine(self, synthetic_experiment):
        # --skip-pdf means no engine is invoked; HTML must still appear.
        assert _run_report(synthetic_experiment) == 0
        html = synthetic_experiment["out_dir"] / "results.html"
        assert html.is_file()
        text = html.read_text(encoding="utf-8", errors="replace")
        assert "Denoise preset sensitivity" in text
        # --embed-resources inlines the figures, so the file stands alone.
        assert "data:image/png;base64," in text

    def test_metrics_are_strict_json_and_schema_tagged(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        text = (synthetic_experiment["out_dir"] / "metrics.json").read_text()
        assert "NaN" not in text
        metrics = json.loads(text)
        assert metrics["schema"] == contract.COMPARISON_METRICS_SCHEMA
        assert metrics["baseline_preset"] == contract.BASELINE_PRESET
        assert metrics["code_revision_consistent"] is True
        assert "no ranking" in metrics["scope"].lower() or "No preset ranking" in metrics["scope"]

    def test_both_comparison_families_present_and_separate(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        metrics = json.loads((synthetic_experiment["out_dir"] / "metrics.json").read_text())
        assert set(metrics["native"]) == set(CATEGORIES)
        assert set(metrics["common_support"]) == set(CATEGORIES)
        for category in CATEGORIES:
            assert set(metrics["native"][category]) == set(PRESETS)
            common = metrics["common_support"][category]
            assert "alignment" in common["mvbs"]
            assert "channels" in common["nasc"]

    def test_native_metrics_carry_retention_and_nasc_totals(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        metrics = json.loads((synthetic_experiment["out_dir"] / "metrics.json").read_text())
        entry = metrics["native"]["short_pulse"]["tpv3"]
        assert entry["retained_pings"] > 0
        assert entry["ping_counts"]["after_crosstalk_prune"] == 185
        totals = [v["total"] for v in entry["nasc"].values()]
        assert all(t is not None and t > 0 for t in totals)

    def test_alignment_loss_is_reported_not_hidden(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        metrics = json.loads((synthetic_experiment["out_dir"] / "metrics.json").read_text())
        nasc = metrics["common_support"]["short_pulse"]["nasc"]["channels"]
        for entry in nasc.values():
            assert entry["baseline_distance_bins"] >= entry["common_distance_bins"]
            assert entry["alignment_loss_fraction"] is not None

    def test_markdown_has_the_required_structure(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        md = (synthetic_experiment["out_dir"] / "results.md").read_text()
        for heading in (
            "## Abstract",
            "## Background",
            "## Prior art",
            "## Methods",
            "### Deviations from Ryan et al. (2015)",
            "## Results — native support",
            "## Common-support comparison",
            "## Interpretation",
            "## Conclusions",
            "## Appendix A — Per-preset echograms",
            "## References",
            "## Reproducibility appendix",
        ):
            assert heading in md, heading

    def test_every_preset_gets_an_echogram_appendix(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        md = (synthetic_experiment["out_dir"] / "results.md").read_text()
        for preset in PRESETS:
            assert f"### A.{PRESETS.index(preset) + 1} `{preset}`" in md
            assert f"--{preset}.png" in md

    def test_interpretation_is_marked_draft(self, synthetic_experiment):
        with scripts_importable():
            import denoise_report_text as text

        _run_report(synthetic_experiment)
        md = (synthetic_experiment["out_dir"] / "results.md").read_text()
        assert text.INTERPRETATION_SIGNOFF_MARKER in md

    def test_deviations_section_names_the_lineage_choices(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        md = (synthetic_experiment["out_dir"] / "results.md").read_text()
        assert "OR-combined, not applied as a sequential cascade" in md
        assert "Fielding-style" in md
        assert "transient_noise_mask_ryan" in md
        assert "percentile" in md

    def test_no_ranking_language(self, synthetic_experiment):
        _run_report(synthetic_experiment)
        md = (synthetic_experiment["out_dir"] / "results.md").read_text().lower()
        for banned in ("best preset", "outperforms", "we recommend using"):
            assert banned not in md

    def test_every_referenced_figure_exists(self, synthetic_experiment):
        import re

        _run_report(synthetic_experiment)
        out = synthetic_experiment["out_dir"]
        md = (out / "results.md").read_text()
        for rel in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md):
            assert (out / rel).is_file(), rel

    def test_sv_domain_figures_are_produced_from_3d_depth(self, synthetic_experiment):
        # Regression: depth arrives as a 3-D data variable on Sv-domain
        # products. Reading only a 1-D depth coordinate silently skipped every
        # echogram, difference and histogram figure.
        _run_report(synthetic_experiment)
        figures = {p.name for p in (synthetic_experiment["out_dir"] / "figures").iterdir()}
        assert any(n.startswith("echogram--") for n in figures)
        assert any(n.startswith("diff--") for n in figures)
        assert any(n.startswith("disagree--") for n in figures)
        assert any(n.startswith("hist--") for n in figures)

    def test_coverage_band_is_actually_applied(self, synthetic_experiment):
        # Regression: without a usable depth profile the band restriction was
        # skipped, so both channels reported the full column as "in band".
        _run_report(synthetic_experiment)
        metrics = json.loads((synthetic_experiment["out_dir"] / "metrics.json").read_text())
        coverage = metrics["native"]["short_pulse"]["tpv3"]["coverage"]
        assert all(c["band_applied"] for c in coverage.values())
        sizes = {c["cells_in_band"] for c in coverage.values()}
        assert len(sizes) > 1, "38 kHz and 200 kHz bands must differ in size"

    def test_missing_run_manifest_is_fatal(self, synthetic_experiment):
        (synthetic_experiment["runs_dir"] / "tpv1" / "run-manifest.json").unlink()
        with pytest.raises(FileNotFoundError, match="No validated run"):
            _run_report(synthetic_experiment)

    def test_reuse_metrics_skips_recomputation(self, synthetic_experiment):
        assert _run_report(synthetic_experiment) == 0
        metrics_path = synthetic_experiment["out_dir"] / "metrics.json"
        payload = json.loads(metrics_path.read_text())
        payload["sentinel"] = "preserved"
        contract.write_json_atomic(metrics_path, payload)

        assert _run_report(synthetic_experiment, ["--reuse-metrics"]) == 0
        assert json.loads(metrics_path.read_text())["sentinel"] == "preserved"
