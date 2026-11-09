"""Raw input onto cluster scratch, and products back to object storage.

Everything streams through this machine over SSH (:mod:`oceanstream.hpc.transfer`):
the cluster may not connect out. Staging never deletes at the destination and a
re-run resumes, skipping files already there at their full size. Scratch only
ever holds a copy of what object storage has, so it is freed as soon as a day's
products are pushed and verified.

Two source layouts:

- ``days``: one folder per day (``<prefix>/<cruise>/<day>/``), as the S3 mirrors write;
- ``flat``: one folder of raw files whose names carry the date (``…D20220621-T120000.raw``),
  as NCEI's bucket and the R2R and Zenodo mirrors hold them.
"""

from __future__ import annotations

import re
import shlex
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath

from .console import HpcError, info, ok, warn
from .remote import Cluster

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_days(days: list[str]) -> list[str]:
    bad = [d for d in days if not DAY_RE.match(d)]
    if bad:
        raise HpcError(f"Days must be YYYY-MM-DD: {bad}")
    return days


def file_patterns(day: str, shift_hours: int = 0) -> list[str]:
    """Raw file names that hold *day* (UTC) in a ``flat`` source.

    With *shift_hours* the recorder's clock was off: true UTC = file time +
    *shift_hours* (e.g. -7 for a clock 7 h fast). The day then spans two file
    dates, and the hour before the window is included too, because a file
    that starts then may run into the day. The pipeline cuts on corrected UTC.
    """
    if not shift_hours:
        return [f"*D{day.replace('-', '')}-T*.raw"]
    start = datetime.combine(date.fromisoformat(day), datetime.min.time())
    first = start - timedelta(hours=shift_hours + 1)
    patterns = []
    for h in range(25):
        t = first + timedelta(hours=h)
        patterns.append(f"*D{t:%Y%m%d}-T{t:%H}*.raw")
    return patterns


def raw_dest(cluster: Cluster, cruise: str, day: str) -> str:
    return f"{cluster.site.raw_dir}/{cruise}/{day}"


def stage_days(  # noqa: PLR0913
        cluster: Cluster, cruise: str, days: list[str], *, src: str = "",
               layout: str = "days", opendata: bool = False, shift_hours: int = 0,
               exclude: list[str] | tuple[str, ...] = ()) -> dict[str, bool]:
    """Stage each day's raw files; returns day -> staged.

    *exclude*: file name patterns left out, e.g. sphere-calibration files that the
    source keeps among the survey files.
    """
    check_days(days)
    if layout not in ("days", "flat"):
        raise HpcError(f"Unknown source layout {layout!r}: use 'days' or 'flat'.")
    site = cluster.site
    result: dict[str, bool] = {}
    excludes = [arg for p in exclude for arg in ("--exclude", p)]
    if layout == "days":
        src = src or site.s3_uri(f"{site.s3_raw_prefix}/{cruise}/")
        includes = [arg for d in days for arg in ("--include", f"{d}/*")]
        rc = cluster.transfer("pull", ["--src", src, "--dest", f"{site.raw_dir}/{cruise}",
                                       *includes, *excludes,
                                       *(["--opendata"] if opendata else [])])
        result = dict.fromkeys(days, rc == 0)
    else:
        if not src:
            raise HpcError("A 'flat' source needs its URI (source.uri in hpc-run.toml).")
        for day in days:
            includes = [arg for p in file_patterns(day, shift_hours) for arg in ("--include", p)]
            rc = cluster.transfer("pull", ["--src", src, "--dest", raw_dest(cluster, cruise, day),
                                           *includes, *excludes,
                                           *(["--opendata"] if opendata else [])])
            result[day] = rc == 0
    for day, staged in result.items():
        (info if staged else warn)(f"{day}: {'staged' if staged else 'staging failed'}")
    return result


def stage_gps(cluster: Cluster, cruise: str, src: str = "") -> None:
    """The cruise's GPS GeoParquet, for ``--gps-dir``."""
    site = cluster.site
    src = src or site.s3_uri(f"{site.s3_gps_prefix}/{cruise}/")
    dest = f"{site.gps_dir}/{cruise}"
    if cluster.transfer("pull", ["--src", src, "--dest", dest]) != 0:
        raise HpcError(f"GPS staging failed from {src}")
    ok(f"GPS staged at {dest}")


def _safe(path: str, kind: str) -> str:
    """Refuse to remove anything but ``…/<kind>/<name>/<YYYY-MM-DD>``."""
    parts = PurePosixPath(path).parts
    too_short = len(parts) < 3  # noqa: PLR2004 - kind/name/day
    if too_short or parts[-3] != kind or not DAY_RE.match(parts[-1]) or not parts[-2]:
        raise HpcError(f"Refusing to remove unexpected path: {path}")
    return path


def unstage(cluster: Cluster, cruise: str, days: list[str]) -> None:
    """Remove the days' raw input from scratch; the source keeps the original."""
    for day in check_days(days):
        path = _safe(raw_dest(cluster, cruise, day), "raw")
        if cluster.xfer_ok(f"rm -rf {shlex.quote(path)}"):
            info(f"{day}: raw removed from scratch")
        else:
            warn(f"{day}: could not remove {path}")


def finished_days(cluster: Cluster, container: str, days: list[str]) -> list[str]:
    """The *days* whose node step has finished: a ``.<day>.DONE`` marker by their products."""
    path = f"{cluster.site.products_dir}/{container}"
    r = cluster.xfer(f"ls -a {shlex.quote(path)} 2>/dev/null || true")
    names = set((r.stdout or "").split())
    return [d for d in days if f".{d}.DONE" in names]


def on_scratch(cluster: Cluster, container: str, day: str) -> bool:
    """Whether the day's products are on scratch."""
    path = f"{cluster.site.products_dir}/{container}/{day}"
    return cluster.xfer_ok(f"test -d {shlex.quote(path)}")


def push_products(  # noqa: PLR0913
        cluster: Cluster, container: str, days: list[str], *, dest: str = "",
                  verify_only: bool = False, replace: bool = False, cleanup: bool = False,
                  streams: int | None = None) -> bool:
    """Push days from scratch to ``s3://<bucket>/<dest>/<day>/``, check, optionally free.

    *dest* is an object-key prefix (default: the products prefix / *container*).
    *replace* also removes what a day no longer has, once every upload succeeded,
    so a reprocessed day replaces the old one exactly. Returns True when every
    requested day is present and verified.
    """
    check_days(days)
    site = cluster.site
    dest = dest or f"{site.s3_products_prefix}/{container}"
    src_root = f"{site.products_dir}/{container}"
    dst_root = site.s3_uri(f"{dest}/")
    present = [d for d in days if on_scratch(cluster, container, d)]
    for d in sorted(set(days) - set(present)):
        warn(f"{d}: nothing at {src_root}/{d}")
    if not present:
        raise HpcError(f"No products to push for {container}: {', '.join(days)}")
    includes = [arg for d in present for arg in ("--include", f"{d}/*")]
    if not verify_only:
        flags = ["--replace", "--always"] if replace else []
        if cluster.transfer("push", ["--src", src_root, "--dest", dst_root, *includes, *flags],
                            streams=streams) != 0:
            raise HpcError(f"Push failed for {container}: {', '.join(present)}")
        ok(f"pushed {len(present)} day(s)")
    if cluster.transfer("check", ["--src", src_root, "--dest", dst_root, *includes]) != 0:
        warn("differences found between scratch and S3")
        return False
    ok(f"S3 matches scratch for {len(present)} day(s)")
    if cleanup:
        for d in present:
            path = _safe(f"{src_root}/{d}", "products")
            if cluster.xfer_ok(f"rm -rf {shlex.quote(path)}"):
                info(f"{d}: scratch freed")
    return len(present) == len(days)
