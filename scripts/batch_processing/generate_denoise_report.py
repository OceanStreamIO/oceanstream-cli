#!/usr/bin/env python3
"""Build the canonical 3-preset denoise sensitivity report for 2023-10-10.

Phase 5 of ``plan-denoisePresetComparison``. Consumes three *validated* preset
runs (each with a ``run-manifest.json``) plus the frozen experiment manifest,
and produces:

    comparison/
      metrics.json      immutable, schema-tagged comparison metrics
      results.md        the canonical report
      results.pdf       pandoc + xelatex build of results.md
      references.bib
      figures/          all figures, fixed shared scales

Two comparison families are computed and kept **explicitly separate**:

* **Native support** (primary) — each preset's real end-to-end products, with
  retained-ping and coverage metrics. This is what a user of that preset would
  actually get.
* **Common support** (secondary) — aligned by pulse category, physical
  frequency, ``ping_time`` and depth; deltas computed only where all three arms
  have finite support. The alignment loss is reported alongside every figure.

Averaging rules: Sv/MVBS are averaged in **linear space** and converted back to
dB; NASC is summed in linear space. There is no bare ``np.nansum`` — every
reduction carries a finite-count guard so missingness is preserved rather than
silently read as zero.

Usage::

    python generate_denoise_report.py \\
        --experiment-manifest .../experiment-manifest.json \\
        --experiment-root ~/oceanstream_experiment/tpos_saildrone_2023 \\
        --runs-dir .../runs --out-dir .../comparison
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import denoise_report_figures as figs
import numpy as np
import xarray as xr
from experiment_contract import (
    BASELINE_PRESET,
    COMPARISON_METRICS_SCHEMA,
    EXPERIMENT_DAY,
    EXPERIMENT_MANIFEST_SCHEMA,
    RUN_MANIFEST_SCHEMA,
    TOLERANCES,
    json_safe,
    read_json,
    write_json_atomic,
)

from oceanstream.echodata.products import open_product_uri

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
)
logger = logging.getLogger("denoise_report")

ASSETS_DIR = Path(__file__).resolve().parent / "report_assets"

#: Cap on rendered echogram pixels — keeps figures readable and the PDF small.
MAX_FIG_COLS = 1800
MAX_FIG_ROWS = 900


# ── Linear-space reductions (no bare nansum) ───────────────────────────────


def db_to_linear(db: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore", invalid="ignore"):
        return np.power(10.0, np.asarray(db, dtype=float) / 10.0)


def linear_to_db(linear: np.ndarray) -> np.ndarray:
    linear = np.asarray(linear, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(linear > 0, 10.0 * np.log10(linear), np.nan)


def guarded_mean_db(db_values: np.ndarray, axis=None, min_count: int = 1):
    """Energy-correct mean of dB values, ``NaN`` when nothing finite contributed.

    ``np.nanmean`` of an all-NaN slice warns and returns NaN; a bare
    ``np.nansum`` would return 0, which is a *value* and would be read as
    "silence" rather than "no data". The explicit finite count keeps the two
    apart.
    """
    linear = db_to_linear(db_values)
    finite = np.isfinite(linear)
    count = finite.sum(axis=axis)
    total = np.where(finite, linear, 0.0).sum(axis=axis)
    mean = np.where(count >= min_count, total / np.maximum(count, 1), np.nan)
    return linear_to_db(mean), count


def guarded_sum_linear(values: np.ndarray, axis=None, min_count: int = 1):
    """Sum in linear space with an explicit finite-count guard."""
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    count = finite.sum(axis=axis)
    total = np.where(finite, values, 0.0).sum(axis=axis)
    return np.where(count >= min_count, total, np.nan), count


# ── Dataset access ─────────────────────────────────────────────────────────


def open_product(root: Path, day: str, category: str, suffix: str) -> xr.Dataset | None:
    path = root / day / f"{day}--{category}{suffix}"
    if not path.exists():
        logger.warning("Missing product %s", path)
        return None
    return open_product_uri(str(path))


def channel_frequencies(ds: xr.Dataset) -> list[float | None]:
    if "frequency_nominal" not in ds:
        return [None] * ds.sizes.get("channel", 1)
    fn = ds["frequency_nominal"]
    extra = [d for d in fn.dims if d != "channel"]
    if extra:
        fn = fn.isel({d: 0 for d in extra})
    return [float(v) for v in np.atleast_1d(fn.values)]


def band_for(freq: float | None) -> tuple[float, float]:
    if freq is None:
        return (10.0, 1000.0)
    band = TOLERANCES.integration_bands_m.get(str(int(round(freq))))
    return (float(band[0]), float(band[1])) if band else (10.0, 1000.0)


def depth_profile(ds: xr.Dataset, ch: int = 0, max_pings: int = 64) -> np.ndarray | None:
    """1-D depth (m) per range bin for one channel.

    Two layouts occur in the same experiment and both must work: stage-4 Sv and
    the denoised/pruned products carry ``depth`` as a 3-D **data variable**
    ``(channel, ping_time, range_sample)``, while MVBS/NASC carry it as a 1-D
    dimension coordinate. Per-ping depth contains NaNs, so the 3-D case is
    reduced with a median over a ping subsample rather than by taking ping 0.
    """
    for name in ("depth", "echo_range"):
        if name in ds.coords and ds[name].ndim == 1:
            return np.asarray(ds[name].values, dtype=float)

    for name in ("depth", "echo_range"):
        if name not in ds:
            continue
        da = ds[name]
        if "channel" in da.dims:
            da = da.isel(channel=ch)
        if "ping_time" in da.dims and da.sizes["ping_time"] > max_pings:
            step = int(np.ceil(da.sizes["ping_time"] / max_pings))
            da = da.isel(ping_time=slice(0, None, step))
        reduce_dims = [d for d in da.dims if d != da.dims[-1]]
        if reduce_dims:
            da = da.median(dim=reduce_dims, skipna=True)
        return np.asarray(da.values, dtype=float).ravel()
    return None


def band_mask(ds: xr.Dataset, ch: int, band: tuple[float, float], n_bins: int):
    """Boolean selector over the range axis for *band*, or ``None`` if unavailable."""
    depth = depth_profile(ds, ch)
    if depth is None or depth.size != n_bins:
        logger.warning(
            "No usable depth profile (got %s for %d range bins) — band %s not applied",
            None if depth is None else depth.size, n_bins, band,
        )
        return None
    return (depth >= band[0]) & (depth <= band[1])


# ── Native-support metrics ─────────────────────────────────────────────────


def native_metrics(
    preset: str, root: Path, day: str, category: str, stats: dict | None
) -> dict:
    """End-to-end products as the preset actually produced them."""
    entry: dict[str, Any] = {"preset": preset, "category": category}

    if stats:
        entry["ping_counts"] = stats.get("ping_counts")
        entry["filters"] = {
            ch["channel"]: {
                "frequency_hz": ch.get("frequency_hz"),
                "depth_band_m": ch.get("depth_band_m"),
                "status": ch.get("filter_status"),
                "denominator": (ch.get("masks") or {}).get("denominator"),
                "per_mask": (ch.get("masks") or {}).get("per_mask"),
                "unique": (ch.get("masks") or {}).get("unique_contribution"),
                "overlap": (ch.get("masks") or {}).get("pairwise_overlap"),
                "union": (ch.get("masks") or {}).get("union"),
                "background_correction": ch.get("background_correction"),
                "sanity_clip": ch.get("sanity_clip"),
                "sv_percentiles": ch.get("sv_percentiles"),
                "mask_union_identity": ch.get("mask_union_identity"),
            }
            for ch in stats.get("channels", [])
        }
        entry["unused_toml_fields"] = stats.get("unused_toml_fields", [])
        entry["resolved_params"] = stats.get("resolved_params", {})

    pruned = open_product(root, day, category, "--pruned.zarr")
    if pruned is not None:
        entry["retained_pings"] = int(pruned.sizes.get("ping_time", 0))
        coverage = {}
        for ch, freq in enumerate(channel_frequencies(pruned)):
            label = str(pruned["channel"].values[ch])
            sv = pruned["Sv"].isel(channel=ch).values
            top, bottom = band_for(freq)
            sel = band_mask(pruned, ch, (top, bottom), sv.shape[-1])
            if sel is not None:
                sv = sv[..., sel]
            finite = int(np.isfinite(sv).sum())
            coverage[label] = {
                "frequency_hz": freq,
                "depth_band_m": [top, bottom],
                "band_applied": sel is not None,
                "cells_in_band": int(sv.size),
                "finite_cells": finite,
                "finite_fraction": finite / sv.size if sv.size else None,
            }
        entry["coverage"] = coverage
        pruned.close()

    nasc = open_product(root, day, category, "--nasc.zarr")
    if nasc is not None:
        entry["nasc"] = integrated_nasc(nasc)
        nasc.close()

    mvbs = open_product(root, day, category, "--mvbs.zarr")
    if mvbs is not None:
        entry["mvbs_profile"] = mvbs_profile(mvbs)
        mvbs.close()

    return entry


def integrated_nasc(nasc: xr.Dataset) -> dict:
    """Depth-integrated NASC per channel, surface bin excluded."""
    out: dict[str, Any] = {}
    if "NASC" not in nasc:
        return out
    depth = np.asarray(nasc["depth"].values, dtype=float)
    keep_from = 1 if TOLERANCES.exclude_surface_bin else 0
    freqs = channel_frequencies(nasc)
    distance = np.asarray(nasc["distance"].values, dtype=float)
    lat = np.asarray(nasc["latitude"].values, dtype=float) if "latitude" in nasc else None
    lon = np.asarray(nasc["longitude"].values, dtype=float) if "longitude" in nasc else None

    for ch, freq in enumerate(freqs):
        label = str(nasc["channel"].values[ch])
        values = np.asarray(nasc["NASC"].isel(channel=ch).values, dtype=float)
        top, bottom = band_for(freq)
        sel = (depth >= top) & (depth <= bottom)
        sel[:keep_from] = False
        per_bin, counts = guarded_sum_linear(values[:, sel], axis=1)
        total, _ = guarded_sum_linear(per_bin)
        out[label] = {
            "frequency_hz": freq,
            "depth_band_m": [top, bottom],
            "surface_bin_excluded": TOLERANCES.exclude_surface_bin,
            "distance_nmi": distance.tolist(),
            "per_bin": per_bin.tolist(),
            "per_bin_finite_depth_cells": counts.tolist(),
            "total": float(total) if np.isfinite(total) else None,
            "latitude": lat.tolist() if lat is not None else None,
            "longitude": lon.tolist() if lon is not None else None,
        }
    return out


def mvbs_profile(mvbs: xr.Dataset) -> dict:
    """Mean MVBS depth profile per channel (linear-space mean, reported in dB)."""
    out: dict[str, Any] = {}
    depth = depth_profile(mvbs)
    if depth is None or "Sv" not in mvbs:
        return out
    for ch, freq in enumerate(channel_frequencies(mvbs)):
        label = str(mvbs["channel"].values[ch])
        sv = np.asarray(mvbs["Sv"].isel(channel=ch).values, dtype=float)
        time_axis = [d for d in mvbs["Sv"].isel(channel=ch).dims].index("ping_time")
        profile_db, counts = guarded_mean_db(sv, axis=time_axis)
        out[label] = {
            "frequency_hz": freq,
            "depth_m": depth.tolist(),
            "mean_sv_db": [None if not np.isfinite(v) else float(v) for v in profile_db],
            "contributing_bins": counts.tolist(),
        }
    return out


# ── Common-support metrics ─────────────────────────────────────────────────


def common_support_mvbs(
    datasets: dict[str, xr.Dataset], category: str
) -> dict:
    """Align MVBS across presets on (frequency, ping_time, depth) and diff.

    Deltas are computed only where **all three** arms are finite. The number of
    baseline cells lost to alignment and to the finite-overlap requirement is
    reported so no figure can quietly hide it.
    """
    if BASELINE_PRESET not in datasets or len(datasets) < 2:
        return {"status": "insufficient_arms"}

    aligned = xr.align(*(datasets[k] for k in datasets), join="inner")
    keys = list(datasets)
    by_key = dict(zip(keys, aligned))

    baseline_full = datasets[BASELINE_PRESET]
    baseline = by_key[BASELINE_PRESET]

    result: dict[str, Any] = {
        "category": category,
        "alignment": {
            "join": "inner",
            "baseline_ping_bins": int(baseline_full.sizes.get("ping_time", 0)),
            "common_ping_bins": int(baseline.sizes.get("ping_time", 0)),
            "baseline_depth_bins": int(baseline_full.sizes.get("depth", 0)),
            "common_depth_bins": int(baseline.sizes.get("depth", 0)),
        },
        "channels": {},
    }
    ping_loss = 1.0 - (
        result["alignment"]["common_ping_bins"]
        / max(1, result["alignment"]["baseline_ping_bins"])
    )
    result["alignment"]["ping_bin_loss_fraction"] = float(ping_loss)

    freqs = channel_frequencies(baseline)
    for ch, freq in enumerate(freqs):
        label = str(baseline["channel"].values[ch])
        arrays = {
            key: np.asarray(ds["Sv"].isel(channel=ch).values, dtype=float)
            for key, ds in by_key.items()
        }
        finite_all = np.ones_like(arrays[BASELINE_PRESET], dtype=bool)
        for arr in arrays.values():
            finite_all &= np.isfinite(arr)

        n_total = finite_all.size
        n_common = int(finite_all.sum())
        n_baseline_finite = int(np.isfinite(arrays[BASELINE_PRESET]).sum())

        channel_entry: dict[str, Any] = {
            "frequency_hz": freq,
            "cells_total": n_total,
            "baseline_finite_cells": n_baseline_finite,
            "common_finite_cells": n_common,
            "common_support_fraction_of_baseline": (
                n_common / n_baseline_finite if n_baseline_finite else None
            ),
            "deltas_db": {},
        }
        for key, arr in arrays.items():
            if key == BASELINE_PRESET:
                continue
            delta = (arr - arrays[BASELINE_PRESET])[finite_all]
            if delta.size == 0:
                channel_entry["deltas_db"][key] = {"status": "no_common_support"}
                continue
            within_tol = int(np.abs(delta).max() <= TOLERANCES.sv_atol_db)
            channel_entry["deltas_db"][key] = {
                "status": "ok",
                "n": int(delta.size),
                "mean": float(delta.mean()),
                "median": float(np.median(delta)),
                "p5": float(np.percentile(delta, 5)),
                "p95": float(np.percentile(delta, 95)),
                "max_abs": float(np.abs(delta).max()),
                "within_declared_tolerance": bool(within_tol),
                "tolerance_db": TOLERANCES.sv_atol_db,
            }
        result["channels"][label] = channel_entry

    return result


def common_support_nasc(native: dict[str, dict], category: str) -> dict:
    """Restrict NASC to distance bins present and finite in every preset."""
    presets = [p for p in native if native[p].get("nasc")]
    if BASELINE_PRESET not in presets or len(presets) < 2:
        return {"status": "insufficient_arms"}

    channels = set.intersection(*(set(native[p]["nasc"]) for p in presets))
    out: dict[str, Any] = {"category": category, "channels": {}}

    for label in sorted(channels):
        per_preset = {p: native[p]["nasc"][label] for p in presets}
        distances = [np.asarray(v["distance_nmi"], dtype=float) for v in per_preset.values()]
        common = distances[0]
        for d in distances[1:]:
            common = np.intersect1d(common, d)

        series: dict[str, np.ndarray] = {}
        for p, v in per_preset.items():
            dist = np.asarray(v["distance_nmi"], dtype=float)
            vals = np.asarray(
                [np.nan if x is None else x for x in v["per_bin"]], dtype=float
            )
            idx = np.searchsorted(dist, common)
            series[p] = vals[idx]

        finite_all = np.ones(common.shape, dtype=bool)
        for arr in series.values():
            finite_all &= np.isfinite(arr)

        baseline_bins = len(per_preset[BASELINE_PRESET]["distance_nmi"])
        entry: dict[str, Any] = {
            "common_distance_nmi": common[finite_all].tolist(),
            "baseline_distance_bins": baseline_bins,
            "common_distance_bins": int(finite_all.sum()),
            "alignment_loss_fraction": (
                1.0 - float(finite_all.sum()) / baseline_bins if baseline_bins else None
            ),
            "totals": {},
            "relative_difference": {},
        }
        for p, arr in series.items():
            total, _ = guarded_sum_linear(arr[finite_all])
            entry["totals"][p] = float(total) if np.isfinite(total) else None

        base_total = entry["totals"].get(BASELINE_PRESET)
        for p, total in entry["totals"].items():
            if p == BASELINE_PRESET or base_total in (None, 0) or total is None:
                continue
            rel = (total - base_total) / base_total
            entry["relative_difference"][p] = {
                "value": float(rel),
                "within_declared_tolerance": bool(abs(rel) <= TOLERANCES.nasc_rtol),
                "rtol": TOLERANCES.nasc_rtol,
            }
        out["channels"][label] = entry

    return out


# ── Metrics assembly ───────────────────────────────────────────────────────


def build_metrics(
    experiment: dict, experiment_root: Path, runs_dir: Path, day: str
) -> dict:
    categories = experiment.get("categories", ["long_pulse", "short_pulse"])
    presets = [p["key"] for p in experiment["presets"]]

    run_manifests: dict[str, dict] = {}
    for key in presets:
        manifest_path = runs_dir / key / "run-manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No validated run for preset {key!r} at {manifest_path}. "
                "Every arm must have completed artifact-matrix validation."
            )
        run_manifests[key] = read_json(manifest_path, RUN_MANIFEST_SCHEMA)

    metrics: dict[str, Any] = {
        "schema": COMPARISON_METRICS_SCHEMA,
        "day": day,
        "baseline_preset": BASELINE_PRESET,
        "scope": (
            "Single-operational-day sensitivity case study. Quantifies how "
            "sensitive the products are to preset choice, faceted by pulse "
            "category and physical frequency. No preset ranking is implied."
        ),
        "tolerances": TOLERANCES.to_dict(),
        "experiment_manifest": {
            "schema": experiment["schema"],
            "source_container": experiment["source_container"],
            "sources": {
                cat: {
                    "sha256": entry["sha256"],
                    "description": entry["description"],
                }
                for cat, entry in experiment["sources"].items()
            },
        },
        "runs": {
            key: {
                "preset_toml": m.get("preset_toml"),
                "preset_toml_sha256": m.get("preset_toml_sha256"),
                "output_container": m.get("output_container"),
                "code_head": (m.get("code") or {}).get("head"),
                "code_dirty": (m.get("code") or {}).get("dirty"),
                "created_utc": m.get("created_utc"),
            }
            for key, m in run_manifests.items()
        },
        "native": {},
        "common_support": {},
    }

    heads = {
        key: (m.get("code") or {}).get("head") for key, m in run_manifests.items()
    }
    metrics["code_revision_consistent"] = len(set(heads.values())) == 1
    if not metrics["code_revision_consistent"]:
        logger.warning("Preset runs used different code revisions: %s", heads)

    for category in categories:
        metrics["native"][category] = {}
        stats_by_preset: dict[str, dict] = {}
        for key in presets:
            root = experiment_root / run_manifests[key]["output_container"]
            stats_path = root / day / f"{day}--{category}--denoise_stats.json"
            stats = read_json(stats_path) if stats_path.exists() else None
            if stats:
                stats_by_preset[key] = stats
            metrics["native"][category][key] = native_metrics(
                key, root, day, category, stats
            )

        mvbs_sets: dict[str, xr.Dataset] = {}
        for key in presets:
            root = experiment_root / run_manifests[key]["output_container"]
            ds = open_product(root, day, category, "--mvbs.zarr")
            if ds is not None:
                mvbs_sets[key] = ds
        try:
            metrics["common_support"][category] = {
                "mvbs": common_support_mvbs(mvbs_sets, category),
                "nasc": common_support_nasc(metrics["native"][category], category),
            }
        finally:
            for ds in mvbs_sets.values():
                ds.close()

    return json_safe(metrics)


# ── Figures ────────────────────────────────────────────────────────────────


def build_figures(
    metrics: dict, experiment: dict, experiment_root: Path, out_dir: Path, day: str
) -> dict:
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[str, list[str]] = {}

    def record(kind: str, path: Path | None) -> None:
        if path is not None:
            produced.setdefault(kind, []).append(str(Path(path).relative_to(out_dir)))

    containers = {k: v["output_container"] for k, v in metrics["runs"].items()}
    source_root = Path(experiment["source_root"])

    for category in metrics["native"]:
        native = metrics["native"][category]

        # 1. Mask-fraction small multiples (per channel)
        channels = sorted(
            {
                ch
                for entry in native.values()
                for ch in (entry.get("filters") or {})
            }
        )
        for label in channels:
            stats = {}
            for preset, entry in native.items():
                filt = (entry.get("filters") or {}).get(label)
                if not filt:
                    continue
                per_mask = filt.get("per_mask") or {}
                unique = filt.get("unique") or {}
                block = {
                    name: {
                        "fraction": (per_mask[name] or {}).get("fraction_of_valid"),
                        "unique": (unique.get(name) or {}).get("fraction_of_valid"),
                    }
                    for name in per_mask
                }
                block["__union__"] = {
                    "fraction": (filt.get("union") or {}).get("fraction_of_valid")
                }
                block["__overlap__"] = {
                    k: (v or {}).get("fraction_of_valid")
                    for k, v in (filt.get("overlap") or {}).items()
                }
                stats[preset] = block
            if stats:
                record(
                    "mask_fractions",
                    figs.mask_fraction_small_multiples(
                        stats,
                        f"{day} · {category} · {_short(label)} — flagged fraction by filter",
                        fig_dir / f"masks--{category}--{_slug(label)}.png",
                    ),
                )

        # 2. MVBS depth profiles overlaying the three presets
        for label in channels:
            profiles = {}
            band = None
            for preset, entry in native.items():
                prof = (entry.get("mvbs_profile") or {}).get(label)
                if not prof:
                    continue
                depth = np.asarray(prof["depth_m"], dtype=float)
                sv = np.asarray(
                    [np.nan if v is None else v for v in prof["mean_sv_db"]], dtype=float
                )
                profiles[preset] = (depth, sv)
                band = band_for(prof.get("frequency_hz"))
            if profiles:
                record(
                    "mvbs_profiles",
                    figs.mvbs_depth_profiles(
                        profiles,
                        f"{day} · {category} · {_short(label)} — mean MVBS profile",
                        fig_dir / f"mvbs-profile--{category}--{_slug(label)}.png",
                        band=band,
                    ),
                )

        # 3. Integrated NASC — native and common support
        common_nasc = (
            metrics["common_support"].get(category, {}).get("nasc", {}) or {}
        )
        for label in channels:
            native_series, common_series = {}, {}
            for preset, entry in native.items():
                nasc = (entry.get("nasc") or {}).get(label)
                if not nasc:
                    continue
                native_series[preset] = (
                    np.asarray(nasc["distance_nmi"], dtype=float),
                    np.asarray(
                        [np.nan if v is None else v for v in nasc["per_bin"]], dtype=float
                    ),
                )
            entry_common = (common_nasc.get("channels") or {}).get(label)
            note = ""
            if entry_common:
                common_dist = np.asarray(entry_common["common_distance_nmi"], dtype=float)
                for preset, (dist, vals) in native_series.items():
                    idx = np.searchsorted(dist, common_dist)
                    idx = np.clip(idx, 0, len(vals) - 1)
                    common_series[preset] = (common_dist, vals[idx])
                loss = entry_common.get("alignment_loss_fraction")
                note = (
                    f"Common support keeps {entry_common['common_distance_bins']} of "
                    f"{entry_common['baseline_distance_bins']} baseline distance bins "
                    f"(alignment loss {loss:.1%})." if loss is not None else ""
                )
            if native_series:
                record(
                    "nasc",
                    figs.integrated_nasc_panels(
                        native_series, common_series,
                        f"{day} · {category} · {_short(label)} — depth-integrated NASC",
                        fig_dir / f"nasc--{category}--{_slug(label)}.png",
                        alignment_note=note,
                    ),
                )

        # 4/5/6. Echogram triptychs, Sv histograms, difference panels
        record_echograms(
            metrics, experiment_root, source_root, containers,
            day, category, fig_dir, record,
        )

    # 7. NASC track map
    tracks = {}
    for preset, entry in metrics["native"].get("short_pulse", {}).items():
        nasc = entry.get("nasc") or {}
        for label, block in nasc.items():
            if block.get("latitude") is None:
                continue
            tracks[preset] = {
                "latitude": np.asarray(block["latitude"], dtype=float),
                "longitude": np.asarray(block["longitude"], dtype=float),
                "nasc": np.asarray(
                    [np.nan if v is None else v for v in block["per_bin"]], dtype=float
                ),
            }
            break
    if tracks:
        record(
            "map",
            figs.nasc_track_map(
                tracks,
                f"{day} · short pulse — track coloured by depth-integrated NASC",
                fig_dir / "nasc-track-map.png",
                natural_earth_dir=ASSETS_DIR / "natural_earth",
            ),
        )

    return produced


def record_echograms(
    metrics, experiment_root: Path, source_root: Path, containers, day, category,
    fig_dir: Path, record,
) -> None:
    """Triptychs, Sv histograms and baseline-difference panels for one category."""
    source_path = source_root / day / f"{day}--{category}.zarr"
    if not source_path.exists():
        logger.warning("No source Sv at %s — skipping echogram figures", source_path)
        return
    src = open_product_uri(str(source_path))

    try:
        freqs = channel_frequencies(src)
        hist_series: dict[str, dict[str, np.ndarray]] = {}

        for ch, freq in enumerate(freqs):
            label = str(src["channel"].values[ch])
            top, bottom = band_for(freq)
            n_bins = src["Sv"].isel(channel=ch).shape[-1]
            depth = depth_profile(src, ch)
            band_sel = band_mask(src, ch, (top, bottom), n_bins)
            if band_sel is None or depth is None:
                logger.warning(
                    "Skipping echogram figures for %s/%s — no usable depth profile",
                    category, label,
                )
                continue

            t_src = np.asarray(src["ping_time"].values)
            sv_src = figs.block_reduce(
                np.asarray(src["Sv"].isel(channel=ch).values, dtype=float)[:, band_sel],
                MAX_FIG_COLS, MAX_FIG_ROWS,
            )
            axes_src = _axes_for(t_src, depth[band_sel], sv_src.shape)

            baseline_panel = None
            for preset, container in containers.items():
                root = experiment_root / container
                pruned = open_product(root, day, category, "--pruned.zarr")
                denoised = open_product(root, day, category, "--denoised.zarr")
                if denoised is None:
                    continue

                sv_den_full = np.asarray(
                    denoised["Sv"].isel(channel=ch).values, dtype=float
                )[:, band_sel]
                sv_den = figs.block_reduce(sv_den_full, MAX_FIG_COLS, MAX_FIG_ROWS)
                axes_den = _axes_for(
                    np.asarray(denoised["ping_time"].values), depth[band_sel], sv_den.shape
                )

                panels = [
                    (f"source Sv (pre-denoise) — {_short(label)}", sv_src, *axes_src),
                    (f"denoised — {preset}", sv_den, *axes_den),
                ]
                if pruned is not None:
                    sv_pruned = figs.block_reduce(
                        np.asarray(pruned["Sv"].isel(channel=ch).values, dtype=float)[
                            :, band_sel
                        ],
                        MAX_FIG_COLS, MAX_FIG_ROWS,
                    )
                    panels.append(
                        (
                            f"pruned (analysis-ready) — {preset}",
                            sv_pruned,
                            *_axes_for(
                                np.asarray(pruned["ping_time"].values),
                                depth[band_sel],
                                sv_pruned.shape,
                            ),
                        )
                    )
                record(
                    "triptych",
                    figs.echogram_triptych(
                        panels,
                        f"{day} · {category} · {preset}",
                        fig_dir / f"echogram--{category}--{_slug(label)}--{preset}.png",
                    ),
                )

                hist_series.setdefault(preset, {})["source"] = _sample(sv_src)
                hist_series[preset]["denoised"] = _sample(sv_den)

                if preset == BASELINE_PRESET:
                    baseline_panel = sv_den
                elif baseline_panel is not None and baseline_panel.shape == sv_den.shape:
                    finite = np.isfinite(baseline_panel) & np.isfinite(sv_den)
                    delta = np.where(finite, sv_den - baseline_panel, np.nan)
                    record(
                        "difference",
                        figs.difference_echogram(
                            delta, *axes_den,
                            f"{day} · {category} · {_short(label)} — {preset} minus {BASELINE_PRESET}",
                            fig_dir / f"diff--{category}--{_slug(label)}--{preset}.png",
                        ),
                    )
                    baseline_flagged = ~np.isfinite(baseline_panel)
                    candidate_flagged = ~np.isfinite(sv_den)
                    codes = np.where(
                        baseline_flagged & candidate_flagged,
                        figs.DISAGREEMENT_CODES["both"],
                        np.where(
                            baseline_flagged,
                            figs.DISAGREEMENT_CODES["baseline_only"],
                            np.where(
                                candidate_flagged,
                                figs.DISAGREEMENT_CODES["candidate_only"],
                                figs.DISAGREEMENT_CODES["agree_unflagged"],
                            ),
                        ),
                    ).astype(float)
                    record(
                        "disagreement",
                        figs.mask_disagreement_map(
                            codes, *axes_den,
                            f"{day} · {category} · {_short(label)} — mask disagreement, "
                            f"{preset} vs {BASELINE_PRESET}",
                            fig_dir / f"disagree--{category}--{_slug(label)}--{preset}.png",
                        ),
                    )

                denoised.close()
                if pruned is not None:
                    pruned.close()

            if hist_series:
                record(
                    "histogram",
                    figs.sv_histogram_overlay(
                        hist_series,
                        f"{day} · {category} · {_short(label)} — Sv distribution pre/post denoise",
                        fig_dir / f"hist--{category}--{_slug(label)}.png",
                    ),
                )
                hist_series = {}
    finally:
        src.close()


def _axes_for(times, depths, shape) -> tuple[np.ndarray, np.ndarray]:
    """Downsampled time/depth axes matching a block-reduced array."""
    t = np.asarray(times)
    d = np.asarray(depths, dtype=float)
    ti = np.linspace(0, max(len(t) - 1, 0), shape[0]).astype(int)
    di = np.linspace(0, max(len(d) - 1, 0), shape[1]).astype(int)
    return t[ti], d[di]


def _sample(arr: np.ndarray, max_n: int = 400_000) -> np.ndarray:
    flat = np.asarray(arr, dtype=float).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size <= max_n:
        return flat
    rng = np.random.default_rng(20231010)
    return rng.choice(flat, size=max_n, replace=False)


def _slug(label: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in label).strip("-")[:48]


def _short(label: str) -> str:
    return label if len(label) <= 34 else label[:31] + "…"


# ── Markdown ───────────────────────────────────────────────────────────────


def render_markdown(
    metrics: dict, experiment: dict, produced: dict, out_dir: Path, day: str
) -> Path:
    from denoise_report_text import build_markdown

    text = build_markdown(metrics, experiment, produced, day)
    path = out_dir / "results.md"
    path.write_text(text, encoding="utf-8")
    logger.info("Wrote %s", path)
    return path


# ── PDF ────────────────────────────────────────────────────────────────────


#: PDF engines in preference order.
#:
#: ``tectonic`` first on purpose: it is a self-contained XeTeX engine that
#: fetches only the packages a document actually needs (a few MB, cached),
#: versus ~7 GB for a full MacTeX install. Being XeTeX-based it handles the
#: same Unicode setup as ``xelatex``.
PDF_ENGINES = ("tectonic", "xelatex", "lualatex")

PDF_ENGINE_HINTS = {
    "tectonic": "brew install tectonic (macOS, ~50 MB) | cargo install tectonic",
    "xelatex": "brew install --cask mactex-no-gui (macOS, ~7 GB) | "
               "apt-get install texlive-xetex texlive-fonts-recommended",
    "lualatex": "part of any TeX Live install",
}


def available_pdf_engine() -> str | None:
    """First installed engine from :data:`PDF_ENGINES`, or ``None``."""
    for engine in PDF_ENGINES:
        if shutil.which(engine):
            return engine
    return None


def preflight_pdf_tools() -> list[str]:
    """Check pandoc and at least one PDF engine. Never installs anything."""
    missing = []
    if shutil.which("pandoc") is None:
        missing.append(
            "pandoc — install with 'brew install pandoc' (macOS) or "
            "'apt-get install pandoc' (Debian/Ubuntu)"
        )
    if available_pdf_engine() is None:
        options = "; ".join(f"{e} → {PDF_ENGINE_HINTS[e]}" for e in PDF_ENGINES)
        missing.append(
            f"a PDF engine — install one of: {options}. "
            "results.html needs none of these."
        )
    return missing


def build_html(md_path: Path, out_dir: Path) -> Path | None:
    """Standalone self-contained HTML — the zero-LaTeX deliverable.

    Built alongside the PDF: it requires no TeX at all, embeds every figure,
    and prints to PDF from any browser when no engine is installed.
    """
    if shutil.which("pandoc") is None:
        return None
    html_path = out_dir / "results.html"
    cmd = [
        "pandoc", str(md_path),
        "-o", str(html_path),
        "--standalone", "--embed-resources",
        "--citeproc", f"--bibliography={out_dir / 'references.bib'}",
        "--resource-path", str(out_dir),
        "--toc", "--toc-depth=2",
        "--metadata", "title=Denoise preset sensitivity — Saildrone TPOS 2023",
    ]
    css = ASSETS_DIR / "report.css"
    if css.exists():
        cmd += ["--css", str(css)]
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True, cwd=str(out_dir)
    )
    if result.returncode != 0:
        logger.error("HTML build failed (%d):\n%s", result.returncode, result.stderr)
        return None
    logger.info("Wrote %s (%.1f MB)", html_path, html_path.stat().st_size / 1e6)
    return html_path


def build_pdf(md_path: Path, out_dir: Path) -> Path | None:
    missing = preflight_pdf_tools()
    if missing:
        logger.error("Cannot build PDF; missing tools:\n  - %s", "\n  - ".join(missing))
        return None

    engine = available_pdf_engine()
    pdf_path = out_dir / "results.pdf"
    cmd = [
        "pandoc", str(md_path),
        "-o", str(pdf_path),
        f"--pdf-engine={engine}",
        "--citeproc",
        f"--bibliography={out_dir / 'references.bib'}",
        "--resource-path", str(out_dir),
        "-V", "geometry:margin=2.2cm",
        "-V", "linkcolor:blue",
        "-V", "fontsize=11pt",
        "--toc", "--toc-depth=2",
    ]
    # Presentation tweaks only. Pandoc's default template owns the preamble the
    # LaTeX it emits depends on; replacing it wholesale broke captionless
    # longtables with "No counter 'none' defined".
    header = ASSETS_DIR / "report-header.tex"
    if header.exists():
        cmd += ["--include-in-header", str(header)]

    logger.info("Building PDF with %s", engine)
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True, cwd=str(out_dir)
    )
    if result.returncode != 0:
        logger.error("%s failed (%d):\n%s", engine, result.returncode, result.stderr)
        return None

    # Zero-error gate: unresolved figures, citations or glyphs fail the build.
    blocking = [
        line for line in result.stderr.splitlines()
        if any(
            token in line.lower()
            for token in ("could not represent", "not found", "could not fetch")
        )
    ]
    if blocking:
        logger.error("PDF built with unresolved resources:\n%s", "\n".join(blocking))
        pdf_path.unlink(missing_ok=True)
        return None
    logger.info("Wrote %s (%.1f MB)", pdf_path, pdf_path.stat().st_size / 1e6)
    return pdf_path


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experiment-manifest", required=True, type=Path)
    parser.add_argument("--experiment-root", required=True, type=Path)
    parser.add_argument("--runs-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--day", default=EXPERIMENT_DAY)
    parser.add_argument(
        "--skip-pdf", action="store_true",
        help="Generate markdown and figures only (PDF build is independent).",
    )
    parser.add_argument(
        "--reuse-metrics", action="store_true",
        help="Reuse an existing metrics.json instead of recomputing it.",
    )
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    experiment = read_json(args.experiment_manifest, EXPERIMENT_MANIFEST_SCHEMA)

    metrics_path = out_dir / "metrics.json"
    if args.reuse_metrics and metrics_path.exists():
        logger.info("Reusing %s", metrics_path)
        metrics = read_json(metrics_path, COMPARISON_METRICS_SCHEMA)
    else:
        metrics = build_metrics(experiment, args.experiment_root, args.runs_dir, args.day)
        write_json_atomic(metrics_path, metrics)
        logger.info("Wrote %s", metrics_path)

    bib_src = ASSETS_DIR / "references.bib"
    if bib_src.exists():
        shutil.copyfile(bib_src, out_dir / "references.bib")

    produced = build_figures(metrics, experiment, args.experiment_root, out_dir, args.day)
    metrics["figures"] = produced
    metrics["natural_earth"] = figs.natural_earth_versions(ASSETS_DIR / "natural_earth")
    write_json_atomic(metrics_path, metrics)

    md_path = render_markdown(metrics, experiment, produced, out_dir, args.day)

    # HTML first: it needs no TeX, so it is always available as a deliverable
    # even when the PDF step cannot run.
    html = build_html(md_path, out_dir)

    if args.skip_pdf:
        logger.info("Skipping PDF build (--skip-pdf)")
        return 0

    pdf = build_pdf(md_path, out_dir)
    if pdf is None:
        logger.error(
            "Markdown and figures are complete at %s%s; the PDF step failed.",
            md_path,
            f" and {html} is ready to view or print" if html else "",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
