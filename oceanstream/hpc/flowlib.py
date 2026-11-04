"""Pure helpers for the HPC flows: no Prefect, no SSH, so they can be unit tested.

oceanstream.hpc.flows imports these; keep anything that talks to the cluster or S3 there.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Run mechanics every cluster run gets, whatever the caller's processing flags.
# Products the web app does not read are skipped; the netCDF copies are what
# the downloads offer.
HPC_RUN_ARGS = [
    "--force",
    "--colormaps", "ocean_r,jet",
    "--skip-combined-echograms", "--save-mvbs-netcdf", "--save-nasc-netcdf",
    "--skip-pmtiles", "--skip-campaign-echograms",
]

# Processing flags of the SD_TPOS2023_v03 reference runs, used when a caller
# gives none.
TPOS_PIPELINE_ARGS = [
    "--expected-categories", "short_pulse,long_pulse",
    "--surface-exclusion-depth", "1.9", "--sv-clip-max-db", "-10.0", "--prune-threshold", "0.8",
    "--mvbs-range-bin", "1m", "--mvbs-ping-time-bin", "10s",
    "--nasc-range-bin", "10m", "--nasc-dist-bin", "0.5nmi",
]

_UNITS = {"KB": 1 / 1024 ** 2, "MB": 1 / 1024, "GB": 1.0, "TB": 1024.0, "PB": 1024.0 ** 2}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x1b]*\x1b\\")


def check(value: str, pattern: re.Pattern, what: str) -> str:
    if not pattern.match(value):
        raise ValueError(f"Invalid {what}: {value!r}")
    return value


def pipeline_args(caller: list[str] | None) -> list[str]:
    """The run mechanics, then the caller's flags (or the TPOS reference ones).

    The caller's flags come last so that, for an option given twice, theirs wins.
    """
    return [*HPC_RUN_ARGS, *(TPOS_PIPELINE_ARGS if caller is None else caller)]


def expand_days(days: list[str] | None, start_date: str, end_date: str) -> list[str]:
    """Sorted, de-duplicated days from a list or an inclusive date range."""
    if days is None:
        if not (start_date and end_date):
            raise ValueError("Give days, or start_date and end_date.")
        d0, d1 = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if d1 < d0:
            raise ValueError(f"end_date {end_date} is before start_date {start_date}.")
        days = [(d0 + timedelta(n)).isoformat() for n in range((d1 - d0).days + 1)]
    return sorted({check(d, DAY_RE, "day") for d in days})


def parse_since(since: str) -> datetime | None:
    """An ISO timestamp; one without a zone is taken as UTC."""
    if not since:
        return None
    t = datetime.fromisoformat(since.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def select_days(
    days: list[str], published: dict[str, datetime], reprocess: bool, since: str = "",
) -> tuple[list[str], list[str]]:
    """Split *days* into (to do, skipped) by when each day's item.json was written.

    Without *reprocess*, a published day is done. With it, every day is redone,
    except that with *since* a day published at or after that time counts as
    already redone, so an interrupted in-place reprocess resumes.
    """
    cutoff = parse_since(since)
    if not reprocess:
        done = set(published)
    elif cutoff is not None:
        done = {d for d, t in published.items() if t >= cutoff}
    else:
        done = set()
    return [d for d in days if d not in done], [d for d in days if d in done]


def batches(days: list[str], size: int) -> list[list[str]]:
    if size < 1:
        raise ValueError(f"batch_size must be at least 1, not {size}.")
    return [days[i:i + size] for i in range(0, len(days), size)]


def web_url(requested: str, allowed: str, default: str) -> str:
    """The web app to import into: *requested* if it is allow-listed, else *default*.

    *allowed* is the comma-separated OCEANSTREAM_WEB_URLS; the default URL is
    always allowed. A flow run cannot make the worker post its ingest key to an
    arbitrary host.
    """
    default = default.rstrip("/")
    if not requested:
        return default
    url = requested.rstrip("/")
    ok = {u.strip().rstrip("/") for u in allowed.split(",") if u.strip()} | {default}
    if url not in ok:
        raise ValueError(f"web_url {requested!r} is not in OCEANSTREAM_WEB_URLS.")
    return url


def preset_file_name(preset: str) -> str:
    """File name for an inline preset; it lands on the cluster under this name.

    The key must change with the content (callers add a hash), because the
    cluster keeps one file per name.
    """
    return f"{check(preset, NAME_RE, 'preset')}.toml"


def parse_bsc_quota(text: str, filesystem: str = "gpfs_scratch") -> tuple[float, float]:
    """(usage, quota) in GB for *filesystem* from `bsc_quota` output."""
    for line in _ANSI.sub("", text).splitlines():
        fields = line.split()
        if fields and fields[0] == filesystem:
            sizes = re.findall(r"([\d.]+)\s*(KB|MB|GB|TB|PB)", line)
            if len(sizes) >= 2:  # noqa: PLR2004 - usage, then quota
                (u, uu), (q, qu) = sizes[:2]
                return float(u) * _UNITS[uu], float(q) * _UNITS[qu]
    raise ValueError(f"No {filesystem} line in bsc_quota output.")
