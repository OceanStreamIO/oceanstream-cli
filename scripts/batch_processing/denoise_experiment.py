#!/usr/bin/env python3
"""Denoise experiments for one day of Sv data.

Tests different denoising method combinations and parameter variations,
generating EK500-colormap echograms for visual comparison.

Usage:
    # Default: 2023-07-21 long_pulse (legacy defaults)
    python denoise_experiment.py

    # Custom day + zarr (e.g. 2023-10-10 long_pulse from external volume)
    python denoise_experiment.py \\
        --zarr /Volumes/RP60/tpos_saildrone_2023/_experiment/local-raw-10oct/2023-10-10/2023-10-10--long_pulse.zarr \\
        --day 2023-10-10 \\
        --out-dir /Volumes/RP60/tpos_saildrone_2023/_experiment/denoised_experiments/2023-10-10--long_pulse

    # Short-pulse (both 38 kHz and 200 kHz channels)
    python denoise_experiment.py \\
        --zarr .../2023-10-10--short_pulse.zarr \\
        --day 2023-10-10 \\
        --channels 0 1 \\
        --out-dir .../denoised_experiments/2023-10-10--short_pulse

    # Run only a subset of experiments
    python denoise_experiment.py --experiments 00_raw 01_full_pipeline 13_all_relaxed
"""
from __future__ import annotations

import argparse
import gc
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from oceanstream.echodata.config import DenoiseConfig
from oceanstream.echodata.denoise import apply_denoising
from echopype.clean import remove_background_noise as ep_remove_background_noise

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("denoise_exp")

# ── Legacy defaults (kept for backward compatibility when script is run with no args) ──
DEFAULT_ZARR_PATH = Path("/tmp/2023-07-21--long_pulse.zarr")
DEFAULT_OUT_DIR = Path(__file__).parent / "denoised_experiments"
DEFAULT_DAY = "2023-07-21"

# ── EK500 colormap ──────────────────────────────────────────────
_EK500_COLORS = [
    (1.000, 1.000, 1.000), (0.624, 0.624, 0.624),
    (0.373, 0.373, 0.686), (0.000, 0.000, 0.498),
    (0.000, 0.000, 0.749), (0.000, 0.498, 0.000),
    (0.000, 0.749, 0.000), (0.498, 0.749, 0.000),
    (0.749, 0.749, 0.000), (0.749, 0.498, 0.000),
    (0.749, 0.000, 0.000), (0.498, 0.000, 0.000),
]
EK500_CMAP = mcolors.LinearSegmentedColormap.from_list("EK500", _EK500_COLORS, N=256)

SV_VMIN = -85.0
SV_VMAX = -50.0
MAX_PLOT_DEPTH = 800.0
COMMON_DEPTH_STEP = 0.5
COMMON_DEPTH_MAX = 1305.0
COMMON_DEPTH = np.arange(0, COMMON_DEPTH_MAX, COMMON_DEPTH_STEP)


# ── Interpolate to common depth grid ────────────────────────────
def interpolate_to_depth(ds: xr.Dataset, ch_idx: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate Sv onto common depth grid. Returns (sv_interp, ping_time, depth)."""
    sv_da = ds["Sv"].isel(channel=ch_idx)
    sv_raw = sv_da.values
    pt = sv_da.ping_time.values
    n_pings = len(pt)

    if "echo_range" in ds:
        er = ds["echo_range"].isel(channel=ch_idx) if "channel" in ds["echo_range"].dims else ds["echo_range"]
        if "ping_time" in er.dims:
            er_vals = np.nanmedian(er.values, axis=0)
        else:
            er_vals = er.values
    else:
        er_vals = np.arange(sv_raw.shape[1], dtype=float)

    sv_interp = np.full((n_pings, len(COMMON_DEPTH)), np.nan, dtype=np.float32)
    valid_depth = ~np.isnan(er_vals)
    er_valid = er_vals[valid_depth]

    if len(er_valid) > 1:
        for i in range(n_pings):
            sv_ping = sv_raw[i, valid_depth]
            mask = ~np.isnan(sv_ping)
            if mask.sum() > 1:
                sv_interp[i] = np.interp(
                    COMMON_DEPTH, er_valid[mask], sv_ping[mask],
                    left=np.nan, right=np.nan,
                )

    return sv_interp, pt, COMMON_DEPTH


# ── Render echogram ─────────────────────────────────────────────
def render_echogram(
    sv_data: np.ndarray,
    ping_time: np.ndarray,
    depth: np.ndarray,
    title: str,
    out_path: Path,
    day: str,
) -> None:
    """Render a time-proportional echogram and save to file."""
    # Trim depth
    has_data = (~np.isnan(sv_data)).any(axis=0)
    if not has_data.any():
        log.warning("  No data for %s — skipping", title)
        return
    last_valid = int(np.where(has_data)[0][-1])
    max_d = min(MAX_PLOT_DEPTH, depth[min(last_valid, len(depth) - 1)] + 10)
    dmask = depth <= max_d
    depth_plot = depth[dmask]
    sv_plot = sv_data[:, :len(depth_plot)]

    n_valid = int((~np.isnan(sv_plot).all(axis=1)).sum())
    n_total = len(ping_time)

    # Time-proportional x-axis
    day_start = np.datetime64(day, "D")
    x_hours = (ping_time - day_start).astype("timedelta64[s]").astype(float) / 3600.0

    time_span = x_hours[-1] - x_hours[0]
    width = min(30, max(12, time_span * 1.2))

    fig, ax = plt.subplots(figsize=(width, 5))
    im = ax.pcolormesh(
        x_hours, depth_plot, sv_plot.T,
        shading="auto", cmap=EK500_CMAP, vmin=SV_VMIN, vmax=SV_VMAX, rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)", fontsize=11)

    # NaN percentage
    nan_pct = 100 * np.isnan(sv_plot).mean()
    ax.set_title(
        f"{day} — {title}\n"
        f"{n_valid}/{n_total} valid pings | {nan_pct:.1f}% NaN",
        fontsize=11, fontweight="bold",
    )

    # Hourly ticks
    h_min = int(np.floor(x_hours[0]))
    h_max = int(np.ceil(x_hours[-1]))
    ticks = list(range(max(0, h_min), min(25, h_max + 1)))
    labels = [f"{h % 24:02d}:00" for h in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Time (UTC)", fontsize=11)
    ax.set_xlim(x_hours[0] - 0.1, x_hours[-1] + 0.1)

    cbar = fig.colorbar(im, ax=ax, fraction=0.015, pad=0.01)
    cbar.set_label("Sv (dB re 1 m⁻¹)", fontsize=10)

    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    log.info("  Saved: %s", out_path.name)


# ── Background noise removal (echopype) ────────────────────────
def apply_bgn(ds: xr.Dataset, ping_num: int = 50, range_sample_num: int = 20,
              snr: str = "3.0dB", noise_max: str | None = None) -> xr.Dataset:
    """Apply echopype background noise removal."""
    ds_copy = ds.copy(deep=True)
    attrs = dict(ds_copy.attrs)
    attrs.setdefault("processing_level", "Level 2A")
    attrs["input_processing_level"] = attrs["processing_level"]
    ds_copy.attrs.update(attrs)

    kwargs = dict(ping_num=ping_num, range_sample_num=range_sample_num, SNR_threshold=snr)
    if noise_max is not None:
        kwargs["background_noise_max"] = noise_max

    result = ep_remove_background_noise(ds_copy, **kwargs)
    sv_var = "Sv_corrected" if "Sv_corrected" in result else "Sv"
    ds_copy["Sv"] = result[sv_var]
    return ds_copy


# ── Experiment definitions ──────────────────────────────────────
@dataclass
class Experiment:
    name: str
    mask_methods: list[str]  # methods for apply_denoising (mask-based)
    config_kwargs: dict       # kwargs for DenoiseConfig
    bgn: bool = True          # apply echopype background noise removal after masks
    bgn_kwargs: dict = None   # override default bgn params

    def __post_init__(self):
        if self.bgn_kwargs is None:
            self.bgn_kwargs = {}


EXPERIMENTS = [
    # 0. Raw (no denoising) — baseline
    Experiment(
        name="00_raw",
        mask_methods=[],
        config_kwargs={},
        bgn=False,
    ),

    # 1. Current production pipeline (all 4 methods)
    Experiment(
        name="01_full_pipeline",
        mask_methods=["impulse", "transient", "attenuation"],
        config_kwargs={},
        bgn=True,
    ),

    # ── Single method experiments ────────────────────────────────

    # 2. Background noise removal only
    Experiment(
        name="02_bgn_only",
        mask_methods=[],
        config_kwargs={},
        bgn=True,
    ),

    # 3. Impulse only (no bgn)
    Experiment(
        name="03_impulse_only",
        mask_methods=["impulse"],
        config_kwargs={},
        bgn=False,
    ),

    # 4. Transient only (no bgn)
    Experiment(
        name="04_transient_only",
        mask_methods=["transient"],
        config_kwargs={},
        bgn=False,
    ),

    # 5. Attenuation only (no bgn)
    Experiment(
        name="05_attenuation_only",
        mask_methods=["attenuation"],
        config_kwargs={},
        bgn=False,
    ),

    # ── Drop one method experiments ──────────────────────────────

    # 6. No transient (impulse + attenuation + bgn)
    Experiment(
        name="06_no_transient",
        mask_methods=["impulse", "attenuation"],
        config_kwargs={},
        bgn=True,
    ),

    # 7. No impulse (transient + attenuation + bgn)
    Experiment(
        name="07_no_impulse",
        mask_methods=["transient", "attenuation"],
        config_kwargs={},
        bgn=True,
    ),

    # 8. No attenuation (impulse + transient + bgn)
    Experiment(
        name="08_no_attenuation",
        mask_methods=["impulse", "transient"],
        config_kwargs={},
        bgn=True,
    ),

    # 9. Masks only, no bgn (impulse + transient + attenuation)
    Experiment(
        name="09_masks_only_no_bgn",
        mask_methods=["impulse", "transient", "attenuation"],
        config_kwargs={},
        bgn=False,
    ),

    # ── Parameter tuning experiments ─────────────────────────────

    # 10. Relaxed transient (higher threshold = less aggressive)
    Experiment(
        name="10_relaxed_transient",
        mask_methods=["impulse", "transient", "attenuation"],
        config_kwargs={"transient_threshold_db": 12.0, "transient_n_pings": 40},
        bgn=True,
    ),

    # 11. Relaxed attenuation (6dB threshold = match 38kHz preset)
    Experiment(
        name="11_relaxed_attenuation_6dB",
        mask_methods=["impulse", "transient", "attenuation"],
        config_kwargs={"attenuation_threshold": 6.0, "attenuation_side_pings": 25},
        bgn=True,
    ),

    # 11b. Very relaxed attenuation (8dB threshold)
    Experiment(
        name="11b_relaxed_attenuation_8dB",
        mask_methods=["impulse", "transient", "attenuation"],
        config_kwargs={"attenuation_threshold": 8.0, "attenuation_side_pings": 25},
        bgn=True,
    ),

    # 12. Relaxed bgn (higher SNR threshold = keep more of the signal)
    Experiment(
        name="12_relaxed_bgn",
        mask_methods=["impulse", "transient", "attenuation"],
        config_kwargs={},
        bgn=True,
        bgn_kwargs={"snr": "6.0dB"},
    ),

    # 13. All relaxed together
    Experiment(
        name="13_all_relaxed",
        mask_methods=["impulse", "transient", "attenuation"],
        config_kwargs={
            "transient_threshold_db": 12.0,
            "transient_n_pings": 40,
            "attenuation_threshold": 0.6,
            "attenuation_side_pings": 25,
        },
        bgn=True,
        bgn_kwargs={"snr": "6.0dB"},
    ),

    # 14. Minimal: impulse + bgn only (skip transient & attenuation entirely)
    Experiment(
        name="14_impulse_bgn_only",
        mask_methods=["impulse"],
        config_kwargs={},
        bgn=True,
    ),

    # 15. Relaxed bgn only (no masks at all, just gentle bgn)
    Experiment(
        name="15_bgn_relaxed_only",
        mask_methods=[],
        config_kwargs={},
        bgn=True,
        bgn_kwargs={"snr": "6.0dB"},
    ),
]


def run_experiment(
    ds: xr.Dataset,
    exp: Experiment,
    ch_idx: int,
    day: str,
    out_dir: Path,
    freq_label: str,
) -> None:
    """Run one denoising experiment and save echogram."""
    t0 = time.perf_counter()
    fname = f"{day}--{exp.name}--{freq_label}--ek500.png"
    out_path = out_dir / fname

    log.info("Experiment: %s [%s]", exp.name, freq_label)

    ds_work = ds.copy(deep=True)

    # Step 1: mask-based denoising
    if exp.mask_methods:
        config = DenoiseConfig(methods=exp.mask_methods, **exp.config_kwargs)
        ds_work = apply_denoising(ds_work, methods=exp.mask_methods, config=config)

    # Step 2: background noise removal (echopype)
    if exp.bgn:
        bgn_kw = {"ping_num": 50, "range_sample_num": 20, "snr": "3.0dB"}
        bgn_kw.update(exp.bgn_kwargs)
        ds_work = apply_bgn(ds_work, **bgn_kw)

    # Interpolate and render
    sv_interp, pt, depth = interpolate_to_depth(ds_work, ch_idx)
    render_echogram(sv_interp, pt, depth, exp.name.replace("_", " "), out_path, day)

    elapsed = time.perf_counter() - t0
    log.info("  Done in %.1fs", elapsed)

    del ds_work, sv_interp
    gc.collect()


def _freq_label_for_channel(ds: xr.Dataset, ch_idx: int) -> str:
    """Return a filename-friendly frequency label like '38kHz' for the given channel index."""
    try:
        freq_hz = float(ds["frequency_nominal"].isel(channel=ch_idx).values)
        khz = int(round(freq_hz / 1000.0))
        return f"{khz}kHz"
    except Exception:
        # Fall back to channel name (may contain '|' etc, sanitise)
        try:
            name = str(ds["channel"].values[ch_idx])
            return name.replace("|", "_").replace("/", "_")
        except Exception:
            return f"ch{ch_idx}"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run denoise experiments on a per-day Sv Zarr.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--zarr", type=Path, default=DEFAULT_ZARR_PATH,
                   help=f"Path to the input Sv zarr store (default: {DEFAULT_ZARR_PATH})")
    p.add_argument("--day", default=None,
                   help="Day label used in filenames + x-axis (YYYY-MM-DD). "
                        "If omitted, inferred from --zarr filename or falls back to default.")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Output directory for echograms. "
                        f"Default: {DEFAULT_OUT_DIR}/<day>--<pulse-category>")
    p.add_argument("--channels", type=int, nargs="+", default=None,
                   help="Channel indices to process (default: all channels in the zarr)")
    p.add_argument("--experiments", nargs="+", default=None,
                   help="Subset of experiment names to run (default: all). "
                        "Names match the leading digits, e.g. '00_raw' or '13_all_relaxed'.")
    return p.parse_args()


def _infer_day(zarr_path: Path, explicit_day: str | None) -> str:
    if explicit_day:
        return explicit_day
    # Try to extract YYYY-MM-DD from the zarr filename
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", zarr_path.name)
    if m:
        return m.group(1)
    return DEFAULT_DAY


def main():
    args = _parse_args()

    zarr_path = args.zarr
    day = _infer_day(zarr_path, args.day)

    # Choose output dir: <default>/<zarr-stem> when not explicit, so multiple
    # zarrs (long_pulse, short_pulse, different days) don't collide.
    if args.out_dir is None:
        out_dir = DEFAULT_OUT_DIR / zarr_path.stem
    else:
        out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Filter experiments by name if requested
    if args.experiments:
        selected = [e for e in EXPERIMENTS if e.name in args.experiments]
        missing = set(args.experiments) - {e.name for e in selected}
        if missing:
            log.warning("Unknown experiment names ignored: %s", sorted(missing))
        experiments = selected
    else:
        experiments = EXPERIMENTS

    log.info("Zarr:        %s", zarr_path)
    log.info("Day:         %s", day)
    log.info("Out dir:     %s", out_dir)
    log.info("Experiments: %d", len(experiments))

    log.info("Loading %s ...", zarr_path)
    ds = xr.open_zarr(str(zarr_path))
    ds = ds.load()
    log.info("Loaded: %s", dict(ds.sizes))

    chans = [str(c) for c in ds.channel.values]
    log.info("Channels: %s", chans)

    ch_indices = args.channels if args.channels else list(range(len(chans)))
    log.info("Processing channel indices: %s", ch_indices)

    for ch_idx in ch_indices:
        if ch_idx < 0 or ch_idx >= len(chans):
            log.error("Channel index %d out of range (0..%d) — skipping", ch_idx, len(chans) - 1)
            continue

        freq_label = _freq_label_for_channel(ds, ch_idx)
        log.info("=== Channel %d (%s) ===", ch_idx, freq_label)

        for exp in experiments:
            try:
                run_experiment(ds, exp, ch_idx, day, out_dir, freq_label)
            except Exception as e:
                log.error("FAILED %s [%s]: %s", exp.name, freq_label, e, exc_info=True)

    ds.close()
    log.info("All experiments complete. Results in %s", out_dir)


if __name__ == "__main__":
    main()
