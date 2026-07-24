#!/usr/bin/env python3
"""Run denoising on a single day and generate raw / denoised / pruned echograms.

Usage (from scripts/batch_processing/):
    python run_denoise_oneday.py [--day 2023-06-25] [--category short_pulse]

Outputs PNG echograms into  local-raw-01/<day>/echograms/
"""
from __future__ import annotations

import argparse
import gc
import logging
import sys
import time
from pathlib import Path

import xarray as xr

# Ensure oceanstream is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from oceanstream.echodata.config import DenoiseConfig
from oceanstream.echodata.denoise import drop_noisy_pings
from oceanstream.echodata.denoise.denoise import apply_denoising
from oceanstream.echodata.plot.echogram import plot_sv_data
from echopype.clean import remove_background_noise as ep_remove_background_noise

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("denoise_oneday")

# ── Tweakable parameters ────────────────────────────────────────
DENOISE_CONFIG = DenoiseConfig(
    use_frequency_specific=True,
    methods=["background", "impulse", "transient", "attenuation"],
    frequency_params={
        38000: {
            "background": {
                "range_window": 30,
                "ping_window": 50,
                "SNR_threshold": "3.0dB",
                "background_noise_max": "-125.0dB",
            },
            "impulse": {
                "vertical_bin_size": "5m",
                "ping_lags": [1, 2],
                "threshold_db": 10.0,
            },
            "transient": {
                "exclude_above": 250.0,
                "depth_bin": 10.0,
                "n_pings": 20,
                "thr_dB": 8.0,
            },
            "attenuation": {
                "upper_limit_sl": 200.0,
                "lower_limit_sl": 400.0,
                "num_side_pings": 15,
                "threshold": 6.0,
            },
        },
        200000: {
            "background": {
                "range_window": 15,
                "ping_window": 40,
                "SNR_threshold": "3.0dB",
                "background_noise_max": "-110.0dB",
            },
            "impulse": {
                "vertical_bin_size": "2m",
                "ping_lags": [1],
                "threshold_db": 8.0,
            },
            "transient": {
                "exclude_above": 100.0,
                "depth_bin": 3.0,
                "n_pings": 10,
                "thr_dB": 5.0,
            },
            "attenuation": {
                "upper_limit_sl": 50.0,
                "lower_limit_sl": 150.0,
                "num_side_pings": 10,
                "threshold": 4.0,
            },
        },
    },
)

DROP_THRESHOLD = 0.95  # fraction NaN above which a ping is pruned
CMAP = "jet"
VMIN, VMAX = -80, -50
DPI = 150


def run(day: str, category: str, base_dir: Path) -> None:
    zarr_path = base_dir / day / f"{day}--{category}.zarr"
    if not zarr_path.exists():
        logger.error("Sv zarr not found: %s", zarr_path)
        sys.exit(1)

    out_dir = base_dir / day / "echograms"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Load raw Sv ──────────────────────────────────────────
    logger.info("Loading %s", zarr_path)
    ds_raw = xr.open_zarr(str(zarr_path))
    logger.info(
        "  shape: channel=%d  ping_time=%d  range_sample=%d",
        ds_raw.sizes.get("channel", 0),
        ds_raw.sizes.get("ping_time", 0),
        ds_raw.sizes.get("range_sample", 0),
    )

    # ── 2. Plot raw echograms ───────────────────────────────────
    logger.info("Plotting raw echograms …")
    t0 = time.perf_counter()
    raw_files = plot_sv_data(
        ds_raw,
        file_base_name=f"{day}--{category}--raw",
        output_path=str(out_dir),
        cmap=CMAP,
        vmin=VMIN,
        vmax=VMAX,
        dpi=DPI,
    )
    logger.info("  Raw echograms (%d files, %.1fs): %s",
                len(raw_files), time.perf_counter() - t0,
                [p.name for p in raw_files])

    # ── 3. Denoise (mask-based: impulse, transient, attenuation) ──
    mask_methods = [m for m in DENOISE_CONFIG.methods if m != "background"]
    logger.info("Running mask-based denoising (methods=%s) …", mask_methods)
    t0 = time.perf_counter()
    ds_denoised, stage_masks = apply_denoising(
        ds_raw,
        methods=mask_methods,
        config=DENOISE_CONFIG,
        merge_masks=True,
        return_stage_masks=True,
    )
    # Force compute mask stats
    for name, mask_da in stage_masks.items():
        pct = float(mask_da.mean().compute()) * 100
        logger.info("  %s mask: %.2f%% flagged", name, pct)
    logger.info("  Mask-based denoising took %.1fs", time.perf_counter() - t0)

    # ── 3b. Background noise removal (modifies Sv values, not mask) ──
    if "background" in DENOISE_CONFIG.methods:
        logger.info("Applying background noise removal per channel …")
        t0 = time.perf_counter()

        if DENOISE_CONFIG.use_frequency_specific:
            bgn_freq_params = DENOISE_CONFIG.to_frequency_keyed_params("background")
        else:
            bgn_freq_params = None
        bgn_global = DENOISE_CONFIG.to_background_params()

        # echopype needs processing_level attrs
        parent_attrs = dict(ds_denoised.attrs)
        parent_attrs.setdefault("processing_level", "Level 2A")
        parent_attrs["input_processing_level"] = parent_attrs["processing_level"]

        def _remove_bgn_one_channel(ch_ds):
            ch_ds.attrs.update(parent_attrs)
            if bgn_freq_params is not None:
                freq = str(int(ch_ds["frequency_nominal"]))
                opts = bgn_freq_params.get(freq, bgn_global)
            else:
                opts = bgn_global
            result = ep_remove_background_noise(
                ch_ds,
                ping_num=opts.get("ping_window", 50),
                range_sample_num=opts.get("range_window", 20),
                SNR_threshold=opts.get("SNR_threshold", "3.0dB"),
                background_noise_max=opts.get("background_noise_max"),
            )
            return result["Sv_corrected"] if "Sv_corrected" in result else result["Sv"]

        # Load into memory — rolling ops create enormous task graphs
        ds_denoised = ds_denoised.load()
        sv_clean = ds_denoised.groupby("channel").map(_remove_bgn_one_channel)
        sv_clean.name = "Sv"
        ds_denoised["Sv"] = sv_clean
        logger.info("  Background noise removal took %.1fs", time.perf_counter() - t0)

    # ── 4. Plot denoised echograms ──────────────────────────────
    logger.info("Plotting denoised echograms …")
    t0 = time.perf_counter()
    den_files = plot_sv_data(
        ds_denoised,
        file_base_name=f"{day}--{category}--denoised",
        output_path=str(out_dir),
        cmap=CMAP,
        vmin=VMIN,
        vmax=VMAX,
        dpi=DPI,
    )
    logger.info("  Denoised echograms (%d files, %.1fs): %s",
                len(den_files), time.perf_counter() - t0,
                [p.name for p in den_files])

    # ── 5. Prune noisy pings, plot ──────────────────────────────
    logger.info("Pruning pings with >%.0f%% NaN …", DROP_THRESHOLD * 100)
    t0 = time.perf_counter()
    ds_pruned = drop_noisy_pings(ds_denoised, drop_threshold=DROP_THRESHOLD)
    n_before = ds_denoised.sizes["ping_time"]
    n_after = ds_pruned.sizes["ping_time"]
    logger.info("  Pings: %d → %d (dropped %d, %.1f%%)",
                n_before, n_after, n_before - n_after,
                (n_before - n_after) / max(n_before, 1) * 100)

    if n_after == 0:
        logger.warning("All pings pruned — skipping pruned echogram")
    else:
        prun_files = plot_sv_data(
            ds_pruned,
            file_base_name=f"{day}--{category}--pruned",
            output_path=str(out_dir),
            cmap=CMAP,
            vmin=VMIN,
            vmax=VMAX,
            dpi=DPI,
        )
        logger.info("  Pruned echograms (%d files, %.1fs): %s",
                     len(prun_files), time.perf_counter() - t0,
                     [p.name for p in prun_files])

    # ── Cleanup ─────────────────────────────────────────────────
    ds_raw.close()
    ds_denoised.close()
    ds_pruned.close()
    del ds_raw, ds_denoised, ds_pruned
    gc.collect()

    logger.info("Done — echograms saved to %s", out_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default="2023-06-25", help="Day key (YYYY-MM-DD)")
    parser.add_argument("--category", default="short_pulse",
                        choices=["short_pulse", "long_pulse"])
    parser.add_argument("--base-dir", default="local-raw-01",
                        help="Base output directory")
    args = parser.parse_args()
    run(args.day, args.category, Path(args.base_dir))


if __name__ == "__main__":
    main()
