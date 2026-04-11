#!/usr/bin/env python3
"""Run stages 9–13 from existing per-day MVBS zarrs.

Use after the main pipeline has completed stages 1–8 (or at least 1–7).

Stages:
  9:  Build combined campaign MVBS zarrs (38 kHz + 200 kHz)
  10: Generate campaign echograms per segment
  11: Echodata PMTiles (acoustic track from Sv zarrs)
  12: NASC Biomass GeoJSON (from per-day NASC zarrs)
  13: NASC Heatmap COGs

Usage:
    python run_stages_9_to_13.py
    python run_stages_9_to_13.py --stages 11,12,13   # only PMTiles + NASC
    python run_stages_9_to_13.py --stages 9,10        # only campaign MVBS + echograms
"""
from __future__ import annotations

import argparse
import gc
import logging
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import matplotlib
matplotlib.use("Agg")

import numpy as np

# Import everything from the main pipeline module
sys.path.insert(0, str(Path(__file__).parent))
from build_full_survey import (
    OUTPUT_DIR, ECHOGRAM_DIR, FREQUENCY_CONFIGS, SURVEY_SEGMENTS,
    COLORMAPS, FREQ_38KHZ, FREQ_200KHZ,
    normalize_string_dtypes, select_frequency, build_combined_zarr,
    plot_combined_echogram, build_echodata_pmtiles,
    build_nasc_biomass_geojson, build_nasc_heatmap_cogs,
    _release_memory, log,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

for _noisy in ("azure", "urllib3", "adlfs", "fsspec", "zarr", "echopype"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def discover_mvbs_zarrs(container_dir: Path) -> dict[str, dict[str, str]]:
    """Scan local disk for existing per-day MVBS zarrs.

    Returns: {day_key: {category: zarr_path}} — same structure
    expected by build_combined_zarr().
    """
    mvbs_re = re.compile(r"(\d{4}-\d{2}-\d{2})--(\w+)--mvbs\.zarr$")
    result: dict[str, dict[str, str]] = {}

    for day_dir in sorted(container_dir.iterdir()):
        if not day_dir.is_dir() or not re.match(r"\d{4}-\d{2}-\d{2}$", day_dir.name):
            continue
        for item in day_dir.iterdir():
            m = mvbs_re.match(item.name)
            if m and item.is_dir():
                day_key = m.group(1)
                category = m.group(2)
                # Store the path relative to the container
                rel_path = f"{day_key}/{item.name}"
                result.setdefault(day_key, {})[category] = rel_path

    return result


def main():
    parser = argparse.ArgumentParser(description="Run stages 9–13 from existing outputs")
    parser.add_argument(
        "--output-container", default="sd-tpos2023-full-v01",
        help="Output container name (default: sd-tpos2023-full-v01)",
    )
    parser.add_argument(
        "--stages", default="9,10,11,12,13",
        help="Comma-separated stage numbers to run (default: 9,10,11,12,13)",
    )
    parser.add_argument(
        "--freq",
        help="Comma-separated frequencies: '38', '200', or '38,200' (default: both)",
    )
    parser.add_argument(
        "--campaign-id", default="saildrone_tpos_2023",
        help="Campaign identifier (default: saildrone_tpos_2023)",
    )
    args = parser.parse_args()

    stages = {int(s.strip()) for s in args.stages.split(",")}
    output_container = args.output_container
    campaign_id = args.campaign_id

    # Determine container directory on local disk
    from build_full_survey import _DATA_DISK
    if _DATA_DISK.exists():
        container_dir = _DATA_DISK / output_container
        # Apply local storage patch
        from local_storage import patch_storage
        patch_storage(_DATA_DISK)
        log.info("Storage: LOCAL → %s", _DATA_DISK)
    else:
        container_dir = OUTPUT_DIR / output_container

    if not container_dir.exists():
        log.error("Container dir not found: %s", container_dir)
        sys.exit(1)

    freq_configs = FREQUENCY_CONFIGS
    if args.freq:
        requested = {f.strip() for f in args.freq.split(",")}
        freq_configs = [fc for fc in FREQUENCY_CONFIGS if any(f in fc[2] for f in requested)]

    pipeline_start = time.time()
    log.info("=" * 70)
    log.info("Stages %s — from existing outputs in %s", args.stages, container_dir)
    log.info("=" * 70)

    # ── Stages 9 + 10: Combined MVBS + campaign echograms ──────
    if 9 in stages or 10 in stages:
        log.info("Discovering per-day MVBS zarrs...")
        day_mvbs_zarrs = discover_mvbs_zarrs(container_dir)
        total_mvbs = sum(len(v) for v in day_mvbs_zarrs.values())
        log.info("Found %d MVBS zarrs across %d days", total_mvbs, len(day_mvbs_zarrs))

        for freq_hz, freq_label, freq_stem, allowed_cats in freq_configs:
            if 9 in stages:
                log.info("=" * 70)
                log.info("STAGE 9: Build combined %s MVBS campaign zarr", freq_label)
                log.info("=" * 70)
                t0 = time.time()

                campaign_ds = build_combined_zarr(
                    day_mvbs_zarrs, output_container,
                    freq_hz=freq_hz, freq_label=freq_label,
                    freq_stem=freq_stem,
                    allowed_categories=allowed_cats,
                    local_save_dir=_DATA_DISK if _DATA_DISK.exists() else None,
                )
                if campaign_ds is None:
                    log.warning("No data for %s — skipping", freq_label)
                    continue

                campaign_ds = campaign_ds.load()
                log.info("Combined %s shape: %s", freq_label, dict(campaign_ds.sizes))

                zarr_path = OUTPUT_DIR / f"campaign_mvbs_combined_{freq_stem}.zarr"
                campaign_ds = normalize_string_dtypes(campaign_ds)
                campaign_ds.to_zarr(str(zarr_path), mode="w")
                log.info("Saved combined zarr: %s", zarr_path)
                log.info("Stage 9 (%s) complete (%.1fs)", freq_label, time.time() - t0)
            else:
                # Load from existing combined zarr
                import xarray as xr
                zarr_path = OUTPUT_DIR / f"campaign_mvbs_combined_{freq_stem}.zarr"
                if not zarr_path.exists():
                    log.warning("No combined zarr for %s, skipping stage 10", freq_label)
                    continue
                campaign_ds = xr.open_zarr(str(zarr_path)).load()

            if 10 in stages:
                log.info("=" * 70)
                log.info("STAGE 10: Generate %s campaign echograms (per segment)", freq_label)
                log.info("=" * 70)
                t0 = time.time()
                ECHOGRAM_DIR.mkdir(parents=True, exist_ok=True)

                for seg_label, seg_start, seg_end in SURVEY_SEGMENTS:
                    seg_start_np = np.datetime64(seg_start)
                    seg_end_np = np.datetime64(seg_end)
                    seg_ds = campaign_ds.sel(
                        ping_time=(campaign_ds.ping_time >= seg_start_np)
                        & (campaign_ds.ping_time < seg_end_np)
                    )
                    n_seg = seg_ds.sizes.get("ping_time", 0)
                    if n_seg == 0:
                        log.warning("  %s: no data — skipping", seg_label)
                        continue

                    log.info("  %s: %s → %s (%d pings)", seg_label, seg_start, seg_end, n_seg)
                    for cmap_name, cmap in COLORMAPS:
                        plot_combined_echogram(
                            seg_ds, cmap_name, cmap,
                            freq_label=freq_label, freq_stem=freq_stem,
                            segment_label=seg_label,
                        )

                log.info("Stage 10 (%s) complete (%.1fs)", freq_label, time.time() - t0)

            campaign_ds.close()
            del campaign_ds
            gc.collect()

    # ── Stage 11: Echodata PMTiles ─────────────────────────────
    if 11 in stages:
        log.info("=" * 70)
        log.info("STAGE 11: Echodata PMTiles (acoustic track)")
        log.info("=" * 70)
        t0 = time.time()

        pmtiles_path = build_echodata_pmtiles(
            output_dir=OUTPUT_DIR,
            container_dir=container_dir,
            campaign_id=campaign_id,
            sample_rate=100,
        )
        if pmtiles_path:
            log.info("Stage 11 complete: %s (%.1fs)", pmtiles_path, time.time() - t0)
        else:
            log.warning("Stage 11 failed (%.1fs)", time.time() - t0)

    # ── Stage 12: NASC Biomass GeoJSON ─────────────────────────
    nasc_geojson_path = None
    if 12 in stages:
        log.info("=" * 70)
        log.info("STAGE 12: NASC Biomass GeoJSON")
        log.info("=" * 70)
        t0 = time.time()

        nasc_geojson_path = build_nasc_biomass_geojson(
            output_dir=OUTPUT_DIR,
            container_dir=container_dir,
            campaign_id=campaign_id,
        )
        if nasc_geojson_path:
            log.info("Stage 12 complete: %s (%.1fs)", nasc_geojson_path, time.time() - t0)
        else:
            log.warning("Stage 12: no output (%.1fs)", time.time() - t0)

    # ── Stage 13: NASC Heatmap COGs ────────────────────────────
    if 13 in stages:
        if nasc_geojson_path is None:
            # Try to find existing GeoJSON
            candidate = OUTPUT_DIR / "nasc_biomass" / f"{campaign_id}.geojson"
            if candidate.exists():
                nasc_geojson_path = candidate

        if nasc_geojson_path:
            log.info("=" * 70)
            log.info("STAGE 13: NASC Heatmap COGs")
            log.info("=" * 70)
            t0 = time.time()
            files = build_nasc_heatmap_cogs(
                geojson_path=nasc_geojson_path,
                output_dir=OUTPUT_DIR,
                campaign_id=campaign_id,
            )
            log.info("Stage 13 complete: %d files (%.1fs)", len(files), time.time() - t0)
        else:
            log.warning("Stage 13: no NASC GeoJSON available — skipping")

    total = time.time() - pipeline_start
    log.info("=" * 70)
    log.info("Complete. Total: %.1fs (%.1f min)", total, total / 60)
    log.info("Output: %s", OUTPUT_DIR)
    log.info("=" * 70)


if __name__ == "__main__":
    main()
