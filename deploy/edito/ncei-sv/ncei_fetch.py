#!/usr/bin/env python3
"""Inputs and outputs for the NCEI Sv job on EDITO.

    raw      download one day of EK80 raw files from the public NCEI water-column bucket
    nav      download an R2R navigation geoCSV and write it as GPS parquet for --gps-dir
    put      upload a file or directory to the project bucket
    compare  compare two Sv stores in the project bucket, then optionally delete the second

The project bucket's credentials, endpoint and region come from the environment
(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL, AWS_DEFAULT_REGION). NCEI is
read anonymously from AWS us-east-1 with an explicit endpoint, so AWS_ENDPOINT_URL does
not redirect it.
"""

# ruff: noqa: PLC0415 - boto3, pandas, s3fs and xarray come from the job image
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

NCEI_BUCKET = "noaa-wcsd-pds"
NCEI_ENDPOINT = "https://s3.us-east-1.amazonaws.com"


def _ncei_client():
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    return boto3.client(
        "s3", region_name="us-east-1", endpoint_url=NCEI_ENDPOINT,
        config=Config(signature_version=UNSIGNED, retries={"max_attempts": 10, "mode": "adaptive"}),
    )


def _project_client():
    import boto3

    return boto3.client("s3", endpoint_url=os.environ["AWS_ENDPOINT_URL"],
                        region_name=os.environ.get("AWS_DEFAULT_REGION"))


def cmd_raw(args: argparse.Namespace) -> int:
    s3 = _ncei_client()
    prefix = f"data/raw/{args.ship}/{args.cruise}/{args.instrument}/"
    stamp = re.compile(r"D" + args.day.replace("-", "") + r"-T\d{6}")
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=NCEI_BUCKET, Prefix=prefix)
    objects = [
        o for page in pages
        for o in page.get("Contents", [])
        if o["Key"].endswith(".raw") and stamp.search(Path(o["Key"]).name)
    ]
    if not objects:
        print(f"No raw files for {args.day} under s3://{NCEI_BUCKET}/{prefix}", file=sys.stderr)
        return 2
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    def fetch(obj: dict) -> int:
        target = dest / Path(obj["Key"]).name
        if target.is_file() and target.stat().st_size == obj["Size"]:
            return 0
        s3.download_file(NCEI_BUCKET, obj["Key"], str(target))
        if target.stat().st_size != obj["Size"]:
            raise OSError(f"Size mismatch for {obj['Key']}")
        return obj["Size"]

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        fetched = sum(pool.map(fetch, objects))
    seconds = time.monotonic() - start
    total = sum(o["Size"] for o in objects)
    print(json.dumps({"day": args.day, "files": len(objects), "bytes": total,
                      "downloaded_bytes": fetched, "seconds": round(seconds, 1),
                      "mb_per_s": round(fetched / 1e6 / max(seconds, 1e-6), 1)}))
    return 0


def cmd_nav(args: argparse.Namespace) -> int:
    import pandas as pd

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    csv_path = dest / Path(args.url).name
    with urllib.request.urlopen(args.url, timeout=120) as response:  # noqa: S310 - fixed NCEI URL
        csv_path.write_bytes(response.read())
    digest = hashlib.md5(csv_path.read_bytes()).hexdigest()  # noqa: S324 - matches the bag manifest
    if args.md5 and digest != args.md5:
        print(f"MD5 mismatch for {csv_path.name}: {digest} != {args.md5}", file=sys.stderr)
        return 2

    df = pd.read_csv(csv_path, comment="#")
    nav = pd.DataFrame({
        "time": pd.to_datetime(df["iso_time"], utc=True).dt.tz_convert(None),
        "latitude": pd.to_numeric(df["ship_latitude"], errors="coerce"),
        "longitude": pd.to_numeric(df["ship_longitude"], errors="coerce"),
        "speed_mps": pd.to_numeric(df["speed_made_good"], errors="coerce"),
        "course_deg": pd.to_numeric(df["course_made_good"], errors="coerce"),
    }).dropna(subset=["latitude", "longitude"]).sort_values("time")
    out = dest / "nav.parquet"
    nav.to_parquet(out, index=False)
    print(json.dumps({"rows": len(nav), "start": str(nav["time"].iloc[0]),
                      "end": str(nav["time"].iloc[-1]), "md5": digest, "parquet": str(out)}))
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    s3 = _project_client()
    src = Path(args.src)
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    for path in files:
        rel = path.name if src.is_file() else str(path.relative_to(src))
        s3.upload_file(str(path), args.bucket, f"{args.key.rstrip('/')}/{rel}")
    print(json.dumps({"uploaded": len(files), "to": f"s3://{args.bucket}/{args.key}"}))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    import numpy as np
    import s3fs
    import xarray as xr

    fs = s3fs.S3FileSystem(client_kwargs={"endpoint_url": os.environ["AWS_ENDPOINT_URL"]})

    def store(root: str) -> str:
        found = fs.glob(f"{root.rstrip('/')}/*--*.zarr")
        if len(found) != 1:
            raise SystemExit(f"Expected one Sv store under {root}, found {found}")
        return found[0]

    a_path, b_path = store(args.a), store(args.b)
    a = xr.open_zarr(fs.get_mapper(a_path), consolidated=None)["Sv"]
    b = xr.open_zarr(fs.get_mapper(b_path), consolidated=None)["Sv"]
    report = {"a": a_path, "b": b_path, "shape_a": list(a.shape), "shape_b": list(b.shape),
              "channels": []}
    if a.shape == b.shape:
        for i, ch in enumerate(a["channel"].values):
            x, y = a.isel(channel=i).values, b.isel(channel=i).values
            fa, fb = np.isfinite(x), np.isfinite(y)
            both = fa & fb
            diff = np.abs(x[both] - y[both])
            report["channels"].append({
                "channel": str(ch),
                "finite_a": int(fa.sum()), "finite_b": int(fb.sum()),
                "nan_pattern_equal": bool((fa == fb).all()),
                "max_abs_diff_db": float(diff.max()) if diff.size else None,
                "p99_abs_diff_db": float(np.percentile(diff, 99)) if diff.size else None,
            })
    print(json.dumps(report, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))
    if args.delete_b:
        fs.rm(args.b.rstrip("/"), recursive=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("raw")
    r.add_argument("--ship", required=True)
    r.add_argument("--cruise", required=True)
    r.add_argument("--day", required=True)
    r.add_argument("--dest", required=True)
    r.add_argument("--instrument", default="EK80")
    r.add_argument("--threads", type=int, default=8)

    n = sub.add_parser("nav")
    n.add_argument("--url", required=True)
    n.add_argument("--dest", required=True)
    n.add_argument("--md5", default="")

    u = sub.add_parser("put")
    u.add_argument("src")
    u.add_argument("--bucket", required=True)
    u.add_argument("--key", required=True)

    c = sub.add_parser("compare")
    c.add_argument("a", help="bucket/prefix of the reference day's Sv")
    c.add_argument("b", help="bucket/prefix of the day's Sv to compare")
    c.add_argument("--out", default="")
    c.add_argument("--delete-b", action="store_true")

    args = p.parse_args()
    return {"raw": cmd_raw, "nav": cmd_nav, "put": cmd_put, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
