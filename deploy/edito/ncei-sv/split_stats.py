#!/usr/bin/env python3
"""Denoise statistics for one day, split by survey segment (station, transit, transition).

Reads the day's Sv store and its denoised (masked-Sv) store from the project bucket and
the survey's segment table, and writes, per channel and segment kind: pings, cells with
data, the share of those cells each step removed, and the median background noise level.
The comparison shows whether station and transit need different presets.

Credentials, endpoint and region come from the environment, as for ncei_fetch.py.
"""

# ruff: noqa: PLC0415 - s3fs, xarray and pandas come from the job image
from __future__ import annotations

import argparse
import json
import os
import sys

BITS = {"impulse": 1, "attenuation": 2, "transient": 4, "background": 8, "clip": 16}


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
    flags_ds = store(args.denoised_container, "*--denoised.zarr")
    with fs.open(f"{root}/{args.segments}", "rb") as fh:
        segments = pd.read_parquet(fh)

    seg_ids = assign_segments(sv["ping_time"].values, segments)
    kind_of = dict(zip(segments["segment_id"], segments["kind"], strict=True))
    kinds = np.array([kind_of.get(s, "outside") for s in seg_ids])

    report = {"day": args.day, "denoised": args.denoised_container, "channels": []}
    for i, channel in enumerate(sv["channel"].values):
        finite = sv["Sv"].isel(channel=i).notnull().values
        flags = flags_ds["denoise_flags"].isel(channel=i).values
        noise = flags_ds["background_noise"].isel(channel=i).values
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
        report["channels"].append(entry)

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps({"day": args.day, "channels": len(report["channels"]), "out": args.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
