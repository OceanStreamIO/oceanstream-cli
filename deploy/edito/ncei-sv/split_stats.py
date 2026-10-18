#!/usr/bin/env python3
"""Denoise statistics for one day, split by survey segment (station, transit, transition).

Reads the day's Sv store and its denoised (masked-Sv) store from the project bucket and
the survey's segment table, and writes, per channel and segment kind: pings, cells with
data, the share of those cells each step removed, and the median background noise level.
The comparison shows whether station and transit need different presets.

Per channel it also measures what is left where there should be nothing: in the deepest
30 % of the recorded range, the share of cells that survive denoising (speckle) and the
share of pings keeping more than 20 % of those cells (streak pings), before and after
pruning. Cells masked as seabed are not water column and count in neither.

Where stage 6 wrote a seabed-masked store, the flags come from it.

Credentials, endpoint and region come from the environment, as for ncei_fetch.py.
"""

# ruff: noqa: PLC0415 - s3fs, xarray and pandas come from the job image
from __future__ import annotations

import argparse
import json
import os
import sys

BITS = {"impulse": 1, "attenuation": 2, "transient": 4, "background": 8, "clip": 16, "seabed": 32}
SEABED = BITS["seabed"]

#: The deepest share of each channel's recorded range, noise-dominated in deep water.
DEEP_FRACTION = 0.3
#: A ping keeping more than this share of its deep band is counted as a streak.
STREAK_KEPT = 0.2


def _quality(sv, i, finite, flags, keep_ping) -> dict:
    """Speckle and streak pings in the channel's deepest DEEP_FRACTION of range."""
    import numpy as np

    pinged = finite.any(axis=1)
    if not pinged.any():
        return {}
    ranges = sv["echo_range"].isel(channel=i, ping_time=int(np.argmax(pinged))).values
    recorded = np.isfinite(ranges)
    deep = recorded & (ranges >= (1 - DEEP_FRACTION) * np.nanmax(ranges))
    cells = finite[:, deep] & ((flags[:, deep] & SEABED) == 0)
    kept = cells & (flags[:, deep] == 0)
    per_ping = kept.sum(axis=1) / np.maximum(cells.sum(axis=1), 1)
    streak = pinged & (per_ping > STREAK_KEPT)
    out = {
        "deep_band_m": [round(float(np.nanmin(ranges[deep])), 1),
                        round(float(np.nanmax(ranges[deep])), 1)],
        "deep_kept_pct": round(100 * float(kept.sum()) / max(int(cells.sum()), 1), 3),
        "streak_pings_pct": round(100 * float(streak.sum()) / int(pinged.sum()), 2),
    }
    if keep_ping is not None:
        survivors = pinged & keep_ping
        out["pings_kept_after_pruning_pct"] = round(
            100 * float(survivors.sum()) / int(pinged.sum()), 2
        )
        out["streak_pings_after_pruning_pct"] = round(
            100 * float((streak & keep_ping).sum()) / max(int(survivors.sum()), 1), 2
        )
    return out


def main() -> int:
    import numpy as np
    import pandas as pd
    import s3fs
    import xarray as xr

    from oceanstream.echodata.survey_segments import assign_segments

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, help="bucket/prefix of the survey, e.g. b/r2r/RR2205")
    p.add_argument("--day", required=True)
    p.add_argument("--sv-container", default="sv")
    p.add_argument("--denoised-container", required=True)
    p.add_argument("--segments", default="segments/segments.parquet")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    fs = s3fs.S3FileSystem(client_kwargs={"endpoint_url": os.environ["AWS_ENDPOINT_URL"]})
    root = args.root.rstrip("/")

    def store(container: str, pattern: str) -> xr.Dataset:
        found = fs.glob(f"{root}/{container}/{args.day}/{pattern}")
        if len(found) != 1:
            raise SystemExit(
                f"Expected one store for {pattern} in {container}/{args.day}, found {found}"
            )
        return xr.open_zarr(fs.get_mapper(found[0]), consolidated=None)

    sv = store(args.sv_container, f"{args.day}--*.zarr")
    noise_ds = store(args.denoised_container, "*--denoised.zarr")
    masked = fs.glob(f"{root}/{args.denoised_container}/{args.day}/*--masked.zarr")
    flags_ds = store(args.denoised_container, "*--masked.zarr") if masked else noise_ds
    pruned = fs.glob(f"{root}/{args.denoised_container}/{args.day}/*--pruned.zarr")
    keep_ping = (
        xr.open_zarr(fs.get_mapper(pruned[0]), consolidated=None)["keep_ping"].values.astype(bool)
        if len(pruned) == 1 else None
    )
    with fs.open(f"{root}/{args.segments}", "rb") as fh:
        segments = pd.read_parquet(fh)

    seg_ids = assign_segments(sv["ping_time"].values, segments)
    kind_of = dict(zip(segments["segment_id"], segments["kind"], strict=True))
    kinds = np.array([kind_of.get(s, "outside") for s in seg_ids])

    report = {"day": args.day, "denoised": args.denoised_container, "channels": []}
    for i, channel in enumerate(sv["channel"].values):
        finite = sv["Sv"].isel(channel=i).notnull().values
        flags = flags_ds["denoise_flags"].isel(channel=i).values
        noise = noise_ds["background_noise"].isel(channel=i).values
        entry = {"channel": str(channel), "kinds": {}}
        for kind in sorted(set(kinds)):
            pings = kinds == kind
            cells = finite[pings]
            n = int(cells.sum())
            if n == 0:
                continue
            f = flags[pings][cells]
            level = noise[pings & finite.any(axis=1)]
            entry["kinds"][kind] = {
                "pings": int((pings & finite.any(axis=1)).sum()),
                "cells": n,
                "removed_pct": {name: round(100 * float(((f & bit) != 0).mean()), 3)
                                for name, bit in BITS.items()},
                "kept_pct": round(100 * float((f == 0).mean()), 3),
                "background_level_db_median": (round(float(np.nanmedian(level)), 2)
                                               if np.isfinite(level).any() else None),
            }
        entry["quality"] = _quality(sv, i, finite, flags, keep_ping)
        report["channels"].append(entry)

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps({"day": args.day, "channels": len(report["channels"]), "out": args.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
