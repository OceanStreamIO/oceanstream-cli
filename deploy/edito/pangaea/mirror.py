#!/usr/bin/env python3
"""Copy the raw echosounder files of a PANGAEA dataset into the project bucket.

PANGAEA keeps large files on tape: the first request for a file answers 503
while it is recalled, and the file is served a few minutes later. Recalls are
started ahead of the downloads (``--prefetch`` files), so they overlap.

Each file streams from PANGAEA into S3 without touching disk; its MD5 is
checked against the dataset's tab export and a mismatch removes the object.
Files already in the bucket at the right size are skipped, so a rerun resumes.

    python mirror.py --dataset 975782 --prefix pangaea/IESSNS2021/raw \\
        --days 2021-07-12,2021-07-16 [--window 2021-07-09T17:00/2021-07-09T21:00]

The bucket comes from OCEANSTREAM_S3_BUCKET, credentials and endpoint from the
AWS_* environment.
"""

# ruff: noqa: PLC0415 - boto3 comes from the job image
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE = "https://download.pangaea.de/dataset"
UA = {"User-Agent": "oceanstream-mirror/1.0 (+https://github.com/OceanStreamIO)"}
NAME_RE = re.compile(r"-D(\d{8})-T(\d{6})\.raw$")


def log(**kw) -> None:
    print(json.dumps({"t": datetime.utcnow().strftime("%H:%M:%S"), **kw}), flush=True)


def file_table(dataset: str) -> dict[str, str]:
    """{raw file name: md5} from the dataset's tab export."""
    req = urllib.request.Request(
        f"https://doi.pangaea.de/10.1594/PANGAEA.{dataset}?format=textfile", headers=UA
    )
    text = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
    rows = list(csv.reader(text.split("*/\n", 1)[1].splitlines(), delimiter="\t"))
    hdr, rows = rows[0], rows[1:]
    i_name, i_md5 = hdr.index("Echo raw"), hdr.index("Echo raw (Hash)")
    return {r[i_name]: r[i_md5] for r in rows if r[i_name].endswith(".raw")}


def start_of(name: str) -> datetime:
    m = NAME_RE.search(name)
    if not m:
        raise ValueError(f"No D<date>-T<time> in {name}")
    return datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S")


def open_url(url: str, headers: dict | None = None, wait_s: float = 60, give_up_s: float = 3600):
    """GET, waiting out 503 (tape recall) and transient errors."""
    t0 = time.time()
    while True:
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=300
            )
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        if time.time() - t0 > give_up_s:
            raise TimeoutError(f"{url} not served after {give_up_s:.0f} s")
        time.sleep(wait_s)


class Hashing(io.RawIOBase):
    """A read-only stream that hashes and counts what passes through it."""

    def __init__(self, raw):
        self.raw, self.md5, self.n = raw, hashlib.md5(), 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self.raw.read(len(b))
        self.md5.update(data)
        self.n += len(data)
        b[: len(data)] = data
        return len(data)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--dataset", required=True, help="PANGAEA dataset number, e.g. 975782")
    p.add_argument("--prefix", required=True, help="Destination key prefix in the project bucket")
    p.add_argument(
        "--days", default="", help="Comma-separated YYYY-MM-DD; files starting on these days"
    )
    p.add_argument("--window", action="append", default=[], help="START/END (UTC, ISO); repeatable")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--prefetch", type=int, default=16, help="Recalls started ahead of the downloads"
    )
    args = p.parse_args()

    import boto3
    from boto3.s3.transfer import TransferConfig

    s3 = boto3.client(
        "s3", endpoint_url=os.environ["AWS_ENDPOINT_URL"], region_name=os.environ.get("AWS_DEFAULT_REGION")
    )
    bucket = os.environ["OCEANSTREAM_S3_BUCKET"]
    table = file_table(args.dataset)
    days = {d for d in args.days.split(",") if d}
    windows = [tuple(datetime.fromisoformat(x) for x in w.split("/")) for w in args.window]
    names = sorted(
        n
        for n in table
        if start_of(n).strftime("%Y-%m-%d") in days or any(a <= start_of(n) < b for a, b in windows)
    )
    log(event="selected", files=len(names), of=len(table))
    if not names:
        return 1

    existing = {}
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=args.prefix.rstrip("/") + "/"
    ):
        existing.update({o["Key"]: o["Size"] for o in page.get("Contents", [])})

    # Recalls run ahead of the downloads: a byte-range request starts one.
    cursor = {"i": 0}
    lock = threading.Lock()

    def prefetcher():
        done = 0
        while done < len(names):
            with lock:
                ahead = cursor["i"] + args.prefetch
            while done < min(ahead, len(names)):
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"{BASE}/{args.dataset}/files/{names[done]}",
                            headers={**UA, "Range": "bytes=0-0"},
                        ),
                        timeout=60,
                    ).close()
                except Exception:  # noqa: BLE001 - a 503 is the point: it starts the recall
                    pass
                done += 1
            time.sleep(20)

    threading.Thread(target=prefetcher, daemon=True).start()
    cfg = TransferConfig(multipart_chunksize=64 * 2**20, max_concurrency=4)

    def copy(name: str) -> dict:
        key = f"{args.prefix.rstrip('/')}/{name}"
        with lock:
            cursor["i"] += 1
        # An object only exists once its upload completed, and a bad MD5 removes it.
        if key in existing:
            return {"file": name, "status": "present", "bytes": existing[key]}
        url = f"{BASE}/{args.dataset}/files/{name}"
        for attempt in (1, 2):
            t0 = time.time()
            resp = open_url(url)
            size = int(resp.headers.get("Content-Length") or 0)
            body = Hashing(resp)
            s3.upload_fileobj(
                io.BufferedReader(body, 8 * 2**20),
                bucket,
                key,
                Config=cfg,
                ExtraArgs={"Metadata": {"md5": table[name]}},
            )
            ok = body.md5.hexdigest() == table[name] and (not size or body.n == size)
            if ok:
                return {
                    "file": name,
                    "status": "copied",
                    "bytes": body.n,
                    "mb_s": round(body.n / 2**20 / max(time.time() - t0, 1e-3), 1),
                    "attempt": attempt,
                }
            s3.delete_object(Bucket=bucket, Key=key)
            log(
                event="mismatch",
                file=name,
                got=body.md5.hexdigest(),
                want=table[name],
                bytes=body.n,
            )
        return {"file": name, "status": "failed"}

    failed, total = 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed([pool.submit(copy, n) for n in names]):
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001 - reported, the rest carries on
                r = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
            failed += r["status"] == "failed"
            total += r.get("bytes", 0)
            log(event="file", **r)
    log(event="done", files=len(names), failed=failed, gb=round(total / 1e9, 1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
