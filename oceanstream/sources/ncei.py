"""Listing raw echosounder cruises: NCEI's public bucket, or a mirror in the project bucket.

Raw file names carry their start time (``…-D20220621-T120000.raw``), so a listing
gives the days a cruise has data on without reading any file. :func:`list_days`
groups a listing by day, optionally after a clock correction, and keeps only the
cruise's own dates (NCEI folders also hold calibration and test files).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

NCEI_BUCKET = "noaa-wcsd-pds"
STAMP_RE = re.compile(r"D(\d{8})-T(\d{6})")


@dataclass
class DayFiles:
    files: int = 0
    bytes: int = 0


def s3_listing(uri: str, *, anonymous: bool = False, region: str = "") -> dict[str, int]:
    """Key name (relative to *uri*) -> size for every object under an ``s3://`` URI."""
    import botocore.session  # noqa: PLC0415 - optional dependency
    from botocore import UNSIGNED  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    bucket, _, prefix = uri.removeprefix("s3://").partition("/")
    session = botocore.session.get_session()
    if anonymous:
        client = session.create_client("s3", region_name=region or "us-east-1",
                                       config=Config(signature_version=UNSIGNED))
    else:
        ep = (os.environ.get("OCEANSTREAM_S3_ENDPOINT") or os.environ.get("AWS_S3_ENDPOINT")
              or os.environ.get("S3_ENDPOINT_URL") or "")
        if ep and "://" not in ep:
            ep = f"https://{ep}"
        client = session.create_client("s3", endpoint_url=ep or None,
                                       region_name=region or os.environ.get("AWS_DEFAULT_REGION"))
    out: dict[str, int] = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            out[obj["Key"][len(prefix):].lstrip("/")] = int(obj["Size"])
    return out


def file_time(name: str) -> datetime | None:
    m = STAMP_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def list_days(listing: dict[str, int] | Iterable[tuple[str, int]], *, start: str = "",
              end: str = "", shift_hours: int = 0) -> dict[str, DayFiles]:
    """Day (UTC, after *shift_hours*) -> raw files and bytes, within [*start*, *end*]."""
    items = listing.items() if isinstance(listing, dict) else listing
    days: dict[str, DayFiles] = {}
    for name, size in items:
        if not name.lower().endswith(".raw"):
            continue
        t = file_time(name)
        if t is None:
            continue
        day = (t + timedelta(hours=shift_hours)).strftime("%Y-%m-%d")
        if (start and day < start) or (end and day > end):
            continue
        d = days.setdefault(day, DayFiles())
        d.files += 1
        d.bytes += size
    return dict(sorted(days.items()))


def summary(days: dict[str, DayFiles]) -> dict[str, Any]:
    return {"days": len(days), "files": sum(d.files for d in days.values()),
            "gb": round(sum(d.bytes for d in days.values()) / 1e9, 1),
            "first": next(iter(days), None), "last": next(reversed(days), None)}
