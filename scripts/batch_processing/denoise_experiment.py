#!/usr/bin/env python3
"""Denoise experiments for 2023-07-21 long_pulse data.

Tests different denoising method combinations and parameter variations,
generating EK500-colormap echograms for visual comparison.

Usage:
    cd scripts/batch_processing
    python denoise_experiment.py
"""
from __future__ import annotations

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

# ── Paths ────────────────────────────────────────────────────────
ZARR_PATH = Path("/tmp/2023-07-21--long_pulse.zarr")
OUT_DIR = Path(__file__).parent / "denoised_experiments"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DAY = "2023-07-21"

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
    day_start = np.datetime64(DAY, "D")
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
        f"{DAY} — {title}\n"
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


def run_experiment(ds: xr.Dataset, exp: Experiment, ch_idx: int = 0) -> None:
    """Run one denoising experiment and save echogram."""
    t0 = time.perf_counter()
    fname = f"{DAY}--{exp.name}--38kHz--ek500.png"
    out_path = OUT_DIR / fname

    log.info("Experiment: %s", exp.name)

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
    render_echogram(sv_interp, pt, depth, exp.name.replace("_", " "), out_path)

    elapsed = time.perf_counter() - t0
    log.info("  Done in %.1fs", elapsed)

    del ds_work, sv_interp
    gc.collect()


def main():
    log.info("Loading %s ...", ZARR_PATH)
    ds = xr.open_zarr(str(ZARR_PATH))
    ds = ds.load()
    log.info("Loaded: %s", dict(ds.sizes))

    # Find 38kHz channel
    chans = [str(c) for c in ds.channel.values]
    log.info("Channels: %s", chans)
    ch_idx = 0  # long_pulse has only 1 channel (38kHz)

    for exp in EXPERIMENTS:
        try:
            run_experiment(ds, exp, ch_idx)
        except Exception as e:
            log.error("FAILED %s: %s", exp.name, e, exc_info=True)

    ds.close()
    log.info("All experiments complete. Results in %s", OUT_DIR)


if __name__ == "__main__":
    main()
