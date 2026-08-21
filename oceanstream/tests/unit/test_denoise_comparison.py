"""Unit tests for the 3-preset denoise comparison scaffolding.

Covers the experiment contract (fingerprints, strict JSON, artifact matrix),
the denoise diagnostics (denominators, overlaps, inert TOML fields), the
split-source / date-filtered resume plumbing, and the report metrics layer.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "batch_processing"

#: Top-level module names the batch scripts would shadow. ``config`` is the big
#: one: ``scripts/batch_processing/config.py`` and the ``oceanstream/config``
#: package both claim it, and leaving the batch one cached breaks every test
#: that does ``importlib.import_module("config.settings")``.
_SHADOWED_MODULES = ("config",)


@contextmanager
def scripts_importable():
    """Make ``scripts/batch_processing`` importable without leaking session state.

    Restores ``sys.path``, the shadowed ``sys.modules`` entries, and
    ``os.environ`` — the last because ``process_from_raw`` calls
    ``load_dotenv()`` at import time.
    """
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
    import denoise_diagnostics as diag  # noqa: E402
    import denoise_report_figures as report_figures  # noqa: E402
    import denoise_report_text as report_text  # noqa: E402
    import experiment_contract as contract  # noqa: E402
    from config import PipelineConfig  # noqa: E402


# ── Experiment contract ────────────────────────────────────────────────────


class TestFingerprinting:
    def test_fingerprint_is_deterministic(self, tmp_path):
        tree = tmp_path / "store"
        (tree / "sub").mkdir(parents=True)
        (tree / "a.bin").write_bytes(b"alpha")
        (tree / "sub" / "b.bin").write_bytes(b"beta")

        first = contract.fingerprint_tree(tree)
        second = contract.fingerprint_tree(tree)
        assert first["sha256"] == second["sha256"]
        assert first["n_files"] == 2

    def test_content_change_changes_fingerprint(self, tmp_path):
        tree = tmp_path / "store"
        tree.mkdir()
        (tree / "a.bin").write_bytes(b"alpha")
        before = contract.fingerprint_tree(tree)["sha256"]
        (tree / "a.bin").write_bytes(b"alphb")
        assert contract.fingerprint_tree(tree)["sha256"] != before

    def test_rename_changes_fingerprint(self, tmp_path):
        tree = tmp_path / "store"
        tree.mkdir()
        (tree / "a.bin").write_bytes(b"alpha")
        before = contract.fingerprint_tree(tree)["sha256"]
        (tree / "a.bin").rename(tree / "renamed.bin")
        assert contract.fingerprint_tree(tree)["sha256"] != before

    def test_ds_store_is_ignored(self, tmp_path):
        tree = tmp_path / "store"
        tree.mkdir()
        (tree / "a.bin").write_bytes(b"alpha")
        before = contract.fingerprint_tree(tree)["sha256"]
        (tree / ".DS_Store").write_bytes(b"macos noise")
        assert contract.fingerprint_tree(tree)["sha256"] == before


class TestStrictJson:
    def test_non_finite_becomes_null_not_nan_token(self, tmp_path):
        path = contract.write_json_atomic(
            tmp_path / "x.json",
            {"schema": "t", "a": float("nan"), "b": float("inf"), "c": 1.5},
        )
        text = path.read_text()
        assert "NaN" not in text and "Infinity" not in text
        payload = json.loads(text)
        assert payload["a"] is None
        assert payload["b"] is None
        assert payload["c"] == 1.5

    def test_numpy_scalars_are_coerced(self, tmp_path):
        payload = {
            "i": np.int64(3),
            "py_int": 7,
            "f": np.float32(2.5),
            "b": np.bool_(True),
            "arr": np.array([1.0, np.nan]),
        }
        out = json.loads(contract.write_json_atomic(tmp_path / "y.json", payload).read_text())
        assert out == {"i": 3, "py_int": 7, "f": 2.5, "b": True, "arr": [1.0, None]}

    def test_int_stays_int_and_bool_stays_bool(self, tmp_path):
        out = json.loads(
            contract.write_json_atomic(tmp_path / "b.json", {"n": 5, "flag": True}).read_text()
        )
        assert out["n"] == 5 and isinstance(out["n"], int)
        assert out["flag"] is True

    def test_partial_write_leaves_no_artifact(self, tmp_path, monkeypatch):
        target = tmp_path / "z.json"

        def _boom(_src, _dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(contract.os, "replace", _boom)
        with pytest.raises(OSError, match="simulated rename failure"):
            contract.write_json_atomic(target, {"schema": "t", "a": 1})

        assert not target.exists()
        assert not list(tmp_path.glob("*.tmp"))

    def test_schema_mismatch_raises(self, tmp_path):
        path = contract.write_json_atomic(tmp_path / "s.json", {"schema": "other/v1"})
        with pytest.raises(contract.StrictJSONError):
            contract.read_json(path, expected_schema="expected/v1")


class TestArtifactMatrix:
    def _make_run(self, root: Path, day: str, categories, products) -> None:
        for category in categories:
            for suffix in products:
                (root / day / f"{day}--{category}{suffix}").mkdir(parents=True, exist_ok=True)

    def test_complete_matrix_reports_nothing(self, tmp_path):
        self._make_run(
            tmp_path, contract.EXPERIMENT_DAY,
            contract.EXPECTED_CATEGORIES, contract.REQUIRED_PRODUCTS,
        )
        assert contract.validate_artifact_matrix(tmp_path) == []

    def test_missing_product_is_reported(self, tmp_path):
        products = [p for p in contract.REQUIRED_PRODUCTS if p != "--nasc.zarr"]
        self._make_run(
            tmp_path, contract.EXPERIMENT_DAY, contract.EXPECTED_CATEGORIES, products
        )
        missing = contract.validate_artifact_matrix(tmp_path)
        assert len(missing) == len(contract.EXPECTED_CATEGORIES)
        assert all("--nasc.zarr" in m for m in missing)

    def test_missing_category_is_reported(self, tmp_path):
        self._make_run(
            tmp_path, contract.EXPERIMENT_DAY, ["short_pulse"], contract.REQUIRED_PRODUCTS
        )
        missing = contract.validate_artifact_matrix(tmp_path)
        assert all("long_pulse" in m for m in missing)

    def test_missing_day_directory_is_reported(self, tmp_path):
        assert contract.validate_artifact_matrix(tmp_path) == [
            f"missing day directory {tmp_path / contract.EXPERIMENT_DAY}"
        ]


class TestTolerances:
    def test_declared_up_front_and_serialisable(self):
        d = contract.TOLERANCES.to_dict()
        assert d["sv_atol_db"] == 0.1
        assert d["nasc_rtol"] == 1e-6
        assert d["equal_nan"] is False
        assert d["exclude_surface_bin"] is True
        assert set(d["integration_bands_m"]) == {"38000", "200000"}
        json.dumps(d, allow_nan=False)

    def test_preset_registry_is_explicit(self):
        assert [p.key for p in contract.PRESETS] == ["ryan-inspired", "tpv1", "tpv3"]
        assert contract.BASELINE_PRESET == "ryan-inspired"
        containers = {p.container for p in contract.PRESETS}
        assert contract.SOURCE_CONTAINER not in containers


# ── Config plumbing ────────────────────────────────────────────────────────


class TestSvSourceContainer:
    def test_default_is_backward_compatible(self):
        cfg = PipelineConfig()
        assert cfg.resolve_sv_source_container("out") == "out"

    def test_split_source_requires_resume_stage_5(self):
        cfg = PipelineConfig(sv_source_container="src", resume_stage=0)
        with pytest.raises(ValueError, match="resume-stage >= 5"):
            cfg.resolve_sv_source_container("out")

    def test_split_source_resolves_when_resuming(self):
        cfg = PipelineConfig(sv_source_container="src", resume_stage=5)
        assert cfg.resolve_sv_source_container("out") == "src"

    def test_source_may_not_equal_destination(self):
        cfg = PipelineConfig(sv_source_container="same", resume_stage=5)
        with pytest.raises(ValueError, match="must differ"):
            cfg.resolve_sv_source_container("same")


# ── Resume reconstruction ──────────────────────────────────────────────────


class _FakeFS:
    """Minimal fsspec-like listing over an in-memory container layout."""

    def __init__(self, container: str, entries: list[str]) -> None:
        self.container = container
        self.entries = entries

    def ls(self, path: str, detail: bool = False):
        if path == self.container:
            days = sorted({e.split("/")[0] for e in self.entries})
            return [f"{self.container}/{d}" for d in days]
        prefix = path[len(self.container) + 1:]
        return [
            f"{self.container}/{e}" for e in self.entries if e.startswith(prefix + "/")
        ]


@pytest.fixture
def fake_container(monkeypatch):
    with scripts_importable():
        import process_from_raw

    entries = []
    for day in ("2023-10-09", "2023-10-10", "2023-10-11"):
        for category in ("long_pulse", "short_pulse"):
            entries.append(f"{day}/{day}--{category}.zarr")
            entries.append(f"{day}/{day}--{category}--denoised.zarr")

    fs = _FakeFS("src", entries)
    monkeypatch.setattr(
        "oceanstream.echodata.storage.get_azure_filesystem", lambda: fs
    )
    return process_from_raw


class TestReconstructionDateFilter:
    def test_no_dates_returns_everything(self, fake_container):
        found = fake_container._reconstruct_day_zarrs("src", suffix=".zarr")
        assert set(found) == {"2023-10-09", "2023-10-10", "2023-10-11"}

    def test_single_day_range_is_respected(self, fake_container):
        found = fake_container._reconstruct_day_zarrs(
            "src", suffix=".zarr",
            start_date=datetime(2023, 10, 10),
            end_date=datetime(2023, 10, 10),
        )
        assert set(found) == {"2023-10-10"}
        assert set(found["2023-10-10"]) == {"long_pulse", "short_pulse"}

    def test_suffix_selects_the_product(self, fake_container):
        found = fake_container._reconstruct_day_zarrs(
            "src", suffix="--denoised.zarr",
            start_date=datetime(2023, 10, 10),
            end_date=datetime(2023, 10, 10),
        )
        assert found["2023-10-10"]["short_pulse"].endswith("--denoised.zarr")

    def test_category_filter_restricts_the_run(self, fake_container):
        found = fake_container._reconstruct_day_zarrs("src", suffix=".zarr")
        filtered = fake_container._filter_categories(found, ["short_pulse"])
        assert all(set(cats) == {"short_pulse"} for cats in filtered.values())

    def test_missing_expected_day_raises(self, fake_container):
        cfg = PipelineConfig(
            start_date=datetime(2023, 10, 12), end_date=datetime(2023, 10, 12)
        )
        with pytest.raises(RuntimeError, match="Missing expected"):
            fake_container._require_expected_days({}, cfg, "src", "source Sv zarrs")

    def test_missing_expected_category_raises(self, fake_container):
        cfg = PipelineConfig(
            start_date=datetime(2023, 10, 10), end_date=datetime(2023, 10, 10)
        )
        partial = {"2023-10-10": {"short_pulse": "path"}}
        with pytest.raises(RuntimeError, match="long_pulse"):
            fake_container._require_expected_days(partial, cfg, "src", "source Sv zarrs")

    def test_complete_set_passes(self, fake_container):
        cfg = PipelineConfig(
            start_date=datetime(2023, 10, 10), end_date=datetime(2023, 10, 10)
        )
        complete = {"2023-10-10": {"short_pulse": "a", "long_pulse": "b"}}
        fake_container._require_expected_days(complete, cfg, "src", "source Sv zarrs")


# ── Denoise diagnostics ────────────────────────────────────────────────────


class TestMaskInteractionStats:
    def _masks(self):
        valid = np.ones((10, 10), dtype=bool)
        a = np.zeros((10, 10), dtype=bool)
        b = np.zeros((10, 10), dtype=bool)
        a[0:3, :] = True          # 30 cells
        b[2:5, :] = True          # 30 cells, 10 shared with a
        return {"impulse": a, "transient": b}, valid

    def test_counts_and_declared_denominator(self):
        masks, valid = self._masks()
        stats = diag.mask_interaction_stats(masks, valid, int(valid.sum()))
        assert stats["denominator"] == 100
        assert stats["per_mask"]["impulse"]["n_flagged"] == 30
        assert stats["per_mask"]["impulse"]["fraction_of_valid"] == pytest.approx(0.30)

    def test_union_is_not_the_sum_of_the_parts(self):
        masks, valid = self._masks()
        stats = diag.mask_interaction_stats(masks, valid, int(valid.sum()))
        parts = sum(v["n_flagged"] for v in stats["per_mask"].values())
        assert stats["union"]["n_flagged"] == 50
        assert parts == 60  # overlap double-counted, as expected

    def test_unique_contributions_sum_to_the_union_minus_shared(self):
        masks, valid = self._masks()
        stats = diag.mask_interaction_stats(masks, valid, int(valid.sum()))
        unique = sum(v["n_unique"] for v in stats["unique_contribution"].values())
        overlap = stats["pairwise_overlap"]["impulse|transient"]["n_overlap"]
        assert overlap == 10
        assert unique + overlap == stats["union"]["n_flagged"]

    def test_denominator_restricts_counts(self):
        masks, valid = self._masks()
        valid[0:2, :] = False  # exclude the first two rows from the band
        stats = diag.mask_interaction_stats(masks, valid, int(valid.sum()))
        assert stats["denominator"] == 80
        assert stats["per_mask"]["impulse"]["n_flagged"] == 10

    def test_zero_denominator_yields_null_fractions(self):
        masks, _ = self._masks()
        valid = np.zeros((10, 10), dtype=bool)
        stats = diag.mask_interaction_stats(masks, valid, 0)
        assert stats["per_mask"]["impulse"]["fraction_of_valid"] is None


class TestMaskUnionIdentity:
    def test_identity_holds(self):
        a = np.zeros((5, 5), dtype=bool)
        b = np.zeros((5, 5), dtype=bool)
        a[0] = True
        b[1] = True
        valid = np.ones((5, 5), dtype=bool)
        result = diag.verify_mask_union(a | b, {"impulse": a, "transient": b}, valid)
        assert result["status"] == "ok"
        assert result["n_mismatch"] == 0

    def test_mismatch_is_counted(self):
        a = np.zeros((5, 5), dtype=bool)
        combined = np.zeros((5, 5), dtype=bool)
        combined[3] = True
        valid = np.ones((5, 5), dtype=bool)
        result = diag.verify_mask_union(combined, {"impulse": a}, valid)
        assert result["status"] == "mismatch"
        assert result["n_mismatch"] == 5


class TestPercentileSummary:
    def test_all_nan_reports_status_not_nan(self):
        summary = diag.percentile_summary(np.full(10, np.nan))
        assert summary["status"] == "no_finite_values"
        assert summary["n_finite"] == 0
        assert all(v is None for v in summary["percentiles_db"].values())

    def test_finite_values_summarised(self):
        summary = diag.percentile_summary(np.linspace(-100, -50, 101))
        assert summary["status"] == "ok"
        assert summary["percentiles_db"]["50"] == pytest.approx(-75.0)


class TestBackgroundAndClipAccounting:
    def _entry(self):
        return {
            "_valid": np.ones((4, 4), dtype=bool),
            "sv_percentiles": {},
        }

    def test_background_is_accounted_as_a_transform_not_a_mask(self):
        entry = self._entry()
        before = np.full((4, 4), -70.0)
        after = before - 0.5
        after[0, 0] = np.nan  # dropped below the SNR threshold
        diag.add_background_stats(entry, before, after)

        bg = entry["background_correction"]
        assert bg["kind"] == "sv_transform"
        assert bg["n_finite_before"] == 16
        assert bg["n_finite_after"] == 15
        assert bg["n_finite_to_nan"] == 1
        assert bg["sv_change_db"]["median"] == pytest.approx(-0.5)
        assert entry["sv_percentiles"]["after_background"]["status"] == "ok"

    def test_background_accepts_the_singleton_channel_axis(self):
        # xarray's groupby("channel") no longer squeezes, so the callback sees
        # (1, ping_time, range_sample) while _valid is 2-D.
        entry = self._entry()
        before = np.full((1, 4, 4), -70.0)
        after = before - 0.5
        diag.add_background_stats(entry, before, after)
        assert entry["background_correction"]["n_finite_before"] == 16
        assert entry["background_correction"]["sv_change_db"]["median"] == pytest.approx(-0.5)

    def test_background_with_no_overlap_reports_status(self):
        entry = self._entry()
        before = np.full((4, 4), np.nan)
        diag.add_background_stats(entry, before, before.copy())
        assert entry["background_correction"]["sv_change_db"]["status"] == (
            "no_overlapping_finite_cells"
        )

    def test_clip_counts_only_newly_removed_cells(self):
        entry = self._entry()
        before = np.full((4, 4), -70.0)
        before[0, :2] = -5.0  # above the clip threshold
        diag.add_clip_stats(entry, before, -10.0)

        clip = entry["sanity_clip"]
        assert clip["threshold_db"] == -10.0
        assert clip["n_newly_clipped"] == 2
        assert clip["fraction_of_valid"] == pytest.approx(2 / 16)

    def test_clip_ignores_cells_already_nan(self):
        entry = self._entry()
        before = np.full((4, 4), -70.0)
        before[1, :] = np.nan
        diag.add_clip_stats(entry, before, -10.0)
        assert entry["sanity_clip"]["n_newly_clipped"] == 0

    def test_clip_respects_the_band_denominator(self):
        entry = self._entry()
        entry["_valid"][0] = False  # first row outside the integration band
        before = np.full((4, 4), -70.0)
        before[0, :2] = -5.0
        diag.add_clip_stats(entry, before, -10.0)
        assert entry["sanity_clip"]["n_newly_clipped"] == 0

    def test_clip_accepts_the_singleton_channel_axis(self):
        entry = self._entry()
        before = np.full((1, 4, 4), -70.0)
        before[0, 0, :2] = -5.0
        diag.add_clip_stats(entry, before, -10.0)
        assert entry["sanity_clip"]["n_newly_clipped"] == 2

    def test_disabled_clip_records_a_null_threshold(self):
        entry = self._entry()
        diag.add_clip_stats(entry, np.full((4, 4), -70.0), None)
        assert entry["sanity_clip"]["threshold_db"] is None
        assert entry["sanity_clip"]["n_newly_clipped"] == 0


class TestChannelGridAlignment:
    def test_matching_shape_is_returned_unchanged(self):
        arr = np.zeros((4, 4))
        assert diag.as_channel_grid(arr, (4, 4)) is arr

    def test_singleton_channel_axis_is_dropped(self):
        arr = np.zeros((1, 4, 4))
        assert diag.as_channel_grid(arr, (4, 4)).shape == (4, 4)

    def test_incompatible_shape_raises(self):
        with pytest.raises(ValueError, match="cannot align"):
            diag.as_channel_grid(np.zeros((2, 4, 4)), (4, 4))


class TestPercentileSampling:
    def test_large_arrays_are_strided_but_report_the_true_finite_count(self):
        values = np.linspace(-100.0, -50.0, 5_000_000)
        summary = diag.percentile_summary(values, max_samples=10_000)
        assert summary["n_finite"] == 5_000_000
        assert summary["n_sampled"] <= 10_000
        assert summary["sample_step"] > 1
        assert summary["percentiles_db"]["50"] == pytest.approx(-75.0, abs=0.1)

    def test_small_arrays_are_not_sampled(self):
        summary = diag.percentile_summary(np.linspace(-100.0, -50.0, 101))
        assert summary["sample_step"] == 1
        assert summary["n_sampled"] == 101


class TestIntegrationBands:
    def test_declared_bands_are_used(self):
        assert diag.band_for_frequency(38000.0) == (10.0, 1000.0)
        assert diag.band_for_frequency(200000.0) == (10.0, 250.0)

    def test_undeclared_frequency_falls_back(self):
        assert diag.band_for_frequency(120000.0) == diag.DEFAULT_BAND_M

    def test_missing_frequency_falls_back(self):
        assert diag.band_for_frequency(None) == diag.DEFAULT_BAND_M


class TestUnusedTomlFields:
    def test_percentile_is_flagged_as_ryan_tn_only(self, tmp_path):
        toml = tmp_path / "preset.toml"
        toml.write_text(
            "[echodata.denoise.frequency_params.38000]\n"
            'transient = { exclude_above = 200.0, depth_bin = 20.0, n_pings = 50,'
            " thr_dB = 15.0, percentile = 15 }\n"
        )
        findings = diag.find_unused_toml_fields(toml)
        assert len(findings) == 1
        assert findings[0]["key"] == "percentile"
        assert "Ryan-TN-only" in findings[0]["reason"]

    def test_consumed_keys_are_not_flagged(self, tmp_path):
        toml = tmp_path / "preset.toml"
        toml.write_text(
            "[echodata.denoise.frequency_params.200000]\n"
            'impulse = { vertical_bin_size = "2m", ping_lags = [1], threshold_db = 8.0 }\n'
        )
        assert diag.find_unused_toml_fields(toml) == []

    def test_shipped_presets_only_flag_percentile(self):
        for preset in contract.PRESETS:
            if not preset.toml_path.exists():
                continue
            keys = {f["key"] for f in diag.find_unused_toml_fields(preset.toml_path)}
            assert keys <= {"percentile"}, (preset.key, keys)

    def test_missing_file_is_empty(self, tmp_path):
        assert diag.find_unused_toml_fields(tmp_path / "nope.toml") == []


class TestFinalise:
    def test_internal_scratch_is_stripped_and_output_is_strict_json(self):
        payload = diag.finalise(
            day_key="2023-10-10",
            category="short_pulse",
            preset_key="tpv3",
            channels=[{"channel": "ch38", "_valid": np.ones((2, 2), dtype=bool),
                       "value": float("nan")}],
            ping_counts={"source": 10},
            resolved_params={},
        )
        assert "_valid" not in payload["channels"][0]
        assert payload["channels"][0]["value"] is None
        assert payload["schema"] == contract.DENOISE_STATS_SCHEMA
        json.dumps(payload, allow_nan=False)


# ── Report metrics ─────────────────────────────────────────────────────────


@pytest.fixture
def report():
    with scripts_importable():
        module = pytest.importorskip("generate_denoise_report")
    return module


class TestGuardedReductions:
    def test_linear_mean_is_energy_correct(self, report):
        # -60 dB and -70 dB: linear mean is 5.5e-7 → -62.6 dB, not -65 dB.
        mean, count = report.guarded_mean_db(np.array([-60.0, -70.0]))
        assert count == 2
        assert mean == pytest.approx(-62.6, abs=0.1)

    def test_all_nan_slice_is_nan_not_zero(self, report):
        mean, count = report.guarded_mean_db(np.array([np.nan, np.nan]))
        assert count == 0
        assert np.isnan(mean)

    def test_sum_of_all_nan_is_nan_not_zero(self, report):
        total, count = report.guarded_sum_linear(np.array([np.nan, np.nan]))
        assert count == 0
        assert np.isnan(total)

    def test_partial_nan_sums_the_finite_part(self, report):
        total, count = report.guarded_sum_linear(np.array([1.0, np.nan, 3.0]))
        assert count == 2
        assert total == pytest.approx(4.0)

    def test_axis_reduction_preserves_missingness_per_row(self, report):
        arr = np.array([[1.0, 2.0], [np.nan, np.nan]])
        total, count = report.guarded_sum_linear(arr, axis=1)
        assert count.tolist() == [2, 0]
        assert total[0] == pytest.approx(3.0)
        assert np.isnan(total[1])


def _mvbs(times, depths, values, freq=38000.0) -> xr.Dataset:
    return xr.Dataset(
        {
            "Sv": (["channel", "ping_time", "depth"], values[None, ...]),
            "frequency_nominal": ("channel", [freq]),
        },
        coords={"channel": ["ch38"], "ping_time": times, "depth": depths},
    )


class TestCommonSupportMvbs:
    def _times(self, n, start="2023-10-10T02:00:00"):
        return pd.date_range(start, periods=n, freq="10s")

    def test_identical_arms_report_zero_delta(self, report):
        times, depths = self._times(6), np.arange(0.0, 30.0, 10.0)
        values = np.full((6, 3), -70.0)
        sets = {
            "ryan-inspired": _mvbs(times, depths, values),
            "tpv1": _mvbs(times, depths, values.copy()),
        }
        out = report.common_support_mvbs(sets, "short_pulse")
        delta = out["channels"]["ch38"]["deltas_db"]["tpv1"]
        assert delta["max_abs"] == pytest.approx(0.0)
        assert delta["within_declared_tolerance"] is True

    def test_mismatched_times_reduce_common_support(self, report):
        depths = np.arange(0.0, 30.0, 10.0)
        base = _mvbs(self._times(10), depths, np.full((10, 3), -70.0))
        shifted = _mvbs(
            self._times(10, "2023-10-10T02:00:30"), depths, np.full((10, 3), -70.0)
        )
        out = report.common_support_mvbs(
            {"ryan-inspired": base, "tpv1": shifted}, "short_pulse"
        )
        assert out["alignment"]["common_ping_bins"] == 7
        assert out["alignment"]["ping_bin_loss_fraction"] == pytest.approx(0.3)

    def test_mismatched_depths_reduce_common_support(self, report):
        times = self._times(4)
        base = _mvbs(times, np.array([0.0, 10.0, 20.0]), np.full((4, 3), -70.0))
        other = _mvbs(times, np.array([10.0, 20.0, 30.0]), np.full((4, 3), -70.0))
        out = report.common_support_mvbs(
            {"ryan-inspired": base, "tpv1": other}, "short_pulse"
        )
        assert out["alignment"]["common_depth_bins"] == 2

    def test_nan_in_one_arm_is_excluded_not_treated_as_equal(self, report):
        times, depths = self._times(4), np.arange(0.0, 30.0, 10.0)
        base = np.full((4, 3), -70.0)
        other = base.copy()
        other[0, 0] = np.nan
        out = report.common_support_mvbs(
            {"ryan-inspired": _mvbs(times, depths, base),
             "tpv1": _mvbs(times, depths, other)},
            "short_pulse",
        )
        channel = out["channels"]["ch38"]
        assert channel["baseline_finite_cells"] == 12
        assert channel["common_finite_cells"] == 11
        assert channel["deltas_db"]["tpv1"]["n"] == 11

    def test_difference_beyond_tolerance_is_flagged(self, report):
        times, depths = self._times(4), np.arange(0.0, 30.0, 10.0)
        out = report.common_support_mvbs(
            {
                "ryan-inspired": _mvbs(times, depths, np.full((4, 3), -70.0)),
                "tpv1": _mvbs(times, depths, np.full((4, 3), -69.0)),
            },
            "short_pulse",
        )
        delta = out["channels"]["ch38"]["deltas_db"]["tpv1"]
        assert delta["median"] == pytest.approx(1.0)
        assert delta["within_declared_tolerance"] is False

    def test_single_arm_is_insufficient(self, report):
        times, depths = self._times(4), np.arange(0.0, 30.0, 10.0)
        out = report.common_support_mvbs(
            {"ryan-inspired": _mvbs(times, depths, np.full((4, 3), -70.0))},
            "short_pulse",
        )
        assert out["status"] == "insufficient_arms"


class TestCommonSupportNasc:
    def _native(self, per_bin_by_preset, distances_by_preset):
        return {
            preset: {
                "nasc": {
                    "ch38": {
                        "frequency_hz": 38000.0,
                        "distance_nmi": distances_by_preset[preset],
                        "per_bin": per_bin,
                    }
                }
            }
            for preset, per_bin in per_bin_by_preset.items()
        }

    def test_identical_arms_have_zero_relative_difference(self, report):
        dist = [0.0, 0.5, 1.0]
        native = self._native(
            {"ryan-inspired": [10.0, 20.0, 30.0], "tpv1": [10.0, 20.0, 30.0]},
            {"ryan-inspired": dist, "tpv1": dist},
        )
        out = report.common_support_nasc(native, "short_pulse")
        rel = out["channels"]["ch38"]["relative_difference"]["tpv1"]
        assert rel["value"] == pytest.approx(0.0)
        assert rel["within_declared_tolerance"] is True

    def test_mismatched_distance_bins_report_alignment_loss(self, report):
        native = self._native(
            {"ryan-inspired": [10.0, 20.0, 30.0], "tpv1": [10.0, 20.0]},
            {"ryan-inspired": [0.0, 0.5, 1.0], "tpv1": [0.0, 0.5]},
        )
        out = report.common_support_nasc(native, "short_pulse")
        entry = out["channels"]["ch38"]
        assert entry["common_distance_bins"] == 2
        assert entry["baseline_distance_bins"] == 3
        assert entry["alignment_loss_fraction"] == pytest.approx(1 / 3)

    def test_null_bins_are_dropped_from_common_support(self, report):
        dist = [0.0, 0.5, 1.0]
        native = self._native(
            {"ryan-inspired": [10.0, None, 30.0], "tpv1": [10.0, 20.0, 30.0]},
            {"ryan-inspired": dist, "tpv1": dist},
        )
        out = report.common_support_nasc(native, "short_pulse")
        entry = out["channels"]["ch38"]
        assert entry["common_distance_bins"] == 2
        assert entry["totals"]["ryan-inspired"] == pytest.approx(40.0)

    def test_relative_difference_is_computed_against_the_baseline(self, report):
        dist = [0.0, 0.5]
        native = self._native(
            {"ryan-inspired": [10.0, 10.0], "tpv1": [11.0, 11.0]},
            {"ryan-inspired": dist, "tpv1": dist},
        )
        out = report.common_support_nasc(native, "short_pulse")
        rel = out["channels"]["ch38"]["relative_difference"]["tpv1"]
        assert rel["value"] == pytest.approx(0.1)
        assert rel["within_declared_tolerance"] is False


class TestBlockReduce:
    def test_linear_mean_downsampling_preserves_shape(self):
        arr = np.full((100, 50), -70.0)
        out = report_figures.block_reduce(arr, 10, 10)
        assert out.shape == (10, 10)
        assert np.allclose(out, -70.0)

    def test_any_reduction_keeps_sparse_flags(self):
        arr = np.zeros((100, 50), dtype=bool)
        arr[3, 4] = True
        out = report_figures.block_reduce(arr, 10, 10, how="any")
        assert out.sum() == 1

    def test_no_downsampling_when_already_small(self):
        arr = np.arange(12.0).reshape(3, 4)
        assert np.array_equal(report_figures.block_reduce(arr, 10, 10), arr)


class TestPdfPreflight:
    def test_preflight_names_missing_tools_with_install_hints(self, report, monkeypatch):
        monkeypatch.setattr(report.shutil, "which", lambda _name: None)
        missing = report.preflight_pdf_tools()
        assert len(missing) == 2
        assert any("pandoc" in m and "brew install" in m for m in missing)
        engine_msg = next(m for m in missing if "PDF engine" in m)
        # Tectonic must be offered, and the 7 GB MacTeX route must not be the
        # only thing a user is told to install.
        assert "tectonic" in engine_msg
        assert "results.html" in engine_msg

    def test_preflight_passes_when_tools_present(self, report, monkeypatch):
        monkeypatch.setattr(report.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert report.preflight_pdf_tools() == []

    def test_tectonic_is_preferred_over_xelatex(self, report, monkeypatch):
        monkeypatch.setattr(
            report.shutil, "which",
            lambda name: f"/usr/bin/{name}" if name in ("tectonic", "xelatex") else None,
        )
        assert report.available_pdf_engine() == "tectonic"

    def test_falls_back_to_xelatex_without_tectonic(self, report, monkeypatch):
        monkeypatch.setattr(
            report.shutil, "which",
            lambda name: f"/usr/bin/{name}" if name == "xelatex" else None,
        )
        assert report.available_pdf_engine() == "xelatex"

    def test_no_engine_returns_none(self, report, monkeypatch):
        monkeypatch.setattr(report.shutil, "which", lambda _name: None)
        assert report.available_pdf_engine() is None

    def test_pandoc_alone_is_enough_for_html(self, report, monkeypatch):
        monkeypatch.setattr(
            report.shutil, "which",
            lambda name: "/usr/bin/pandoc" if name == "pandoc" else None,
        )
        # HTML needs no TeX at all, so only the engine entry should be missing.
        missing = report.preflight_pdf_tools()
        assert len(missing) == 1
        assert "PDF engine" in missing[0]


class TestInterpretationSignoff:
    def test_interpretation_stays_marked_draft(self):
        metrics = {
            "common_support": {
                "short_pulse": {
                    "mvbs": {
                        "channels": {
                            "ch38": {
                                "frequency_hz": 38000.0,
                                "common_support_fraction_of_baseline": 0.9,
                                "deltas_db": {
                                    "tpv1": {
                                        "status": "ok", "n": 10, "median": 0.2,
                                        "p5": -0.1, "p95": 0.5, "max_abs": 0.7,
                                        "within_declared_tolerance": False,
                                    }
                                },
                            }
                        }
                    },
                    "nasc": {"channels": {}},
                }
            }
        }
        rendered = report_text._interpretation(metrics)
        assert report_text.INTERPRETATION_SIGNOFF_MARKER in rendered
        assert "outside the declared tolerance" in rendered
        for banned in ("best", "recommend", "superior", "should use"):
            assert banned not in rendered.lower()
