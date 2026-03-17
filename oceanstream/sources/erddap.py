"""Lightweight ERDDAP REST client for tabledap CSV downloads.

Provides helpers to list datasets, fetch metadata, and download CSV data
from any ERDDAP server (EMSO, EMODnet Physics, etc.).  Uses only the
standard library + ``pandas`` so no extra dependencies are required.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class DatasetInfo:
    """Minimal descriptor for an ERDDAP dataset."""

    dataset_id: str
    title: str = ""
    summary: str = ""
    min_time: str = ""
    max_time: str = ""
    min_latitude: float | None = None
    max_latitude: float | None = None
    min_longitude: float | None = None
    max_longitude: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _fetch_text(url: str, timeout: int = 60) -> str:
    """Fetch *url* and return the response body as text."""
    req = urllib.request.Request(url, headers={"User-Agent": "oceanstream/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def list_datasets(server_url: str, *, timeout: int = 120) -> list[DatasetInfo]:
    """Return a list of datasets from an ERDDAP server.

    Queries the ``allDatasets`` endpoint with a minimal set of columns.

    Parameters
    ----------
    server_url:
        Base URL of the ERDDAP server, e.g. ``https://erddap.emso.eu/erddap``.
    timeout:
        HTTP timeout in seconds.
    """
    base = server_url.rstrip("/")
    url = (
        f"{base}/tabledap/allDatasets.csv"
        "?datasetID,title,summary,minTime,maxTime,"
        "minLatitude,maxLatitude,minLongitude,maxLongitude"
    )
    text = _fetch_text(url, timeout=timeout)
    reader = csv.DictReader(io.StringIO(text))

    datasets: list[DatasetInfo] = []
    for row in reader:
        # ERDDAP returns a units row as the first data row — skip it
        if row.get("datasetID", "").startswith("datasetID"):
            continue

        def _float(val: str | None) -> float | None:
            if val is None or val.strip() == "":
                return None
            try:
                return float(val)
            except ValueError:
                return None

        datasets.append(
            DatasetInfo(
                dataset_id=row.get("datasetID", ""),
                title=row.get("title", ""),
                summary=row.get("summary", ""),
                min_time=row.get("minTime", ""),
                max_time=row.get("maxTime", ""),
                min_latitude=_float(row.get("minLatitude")),
                max_latitude=_float(row.get("maxLatitude")),
                min_longitude=_float(row.get("minLongitude")),
                max_longitude=_float(row.get("maxLongitude")),
            )
        )
    return datasets


def get_dataset_metadata(
    server_url: str,
    dataset_id: str,
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    """Return column names, units, and global attributes for a dataset.

    Fetches the *datasetID* ``/info`` endpoint which provides variable
    metadata in CSV form.
    """
    base = server_url.rstrip("/")
    url = f"{base}/info/{urllib.parse.quote(dataset_id)}/index.csv"
    text = _fetch_text(url, timeout=timeout)
    reader = csv.DictReader(io.StringIO(text))

    metadata: dict[str, Any] = {"variables": {}, "global": {}}
    for row in reader:
        row_type = row.get("Row Type", "")
        var_name = row.get("Variable Name", "")
        attr = row.get("Attribute Name", "")
        value = row.get("Value", "")
        if row_type == "variable":
            metadata["variables"].setdefault(var_name, {})
        elif row_type == "attribute" and var_name:
            metadata["variables"].setdefault(var_name, {})[attr] = value
        elif row_type == "attribute" and not var_name:
            metadata["global"][attr] = value
    return metadata


def download_csv(  # noqa: PLR0913
    server_url: str,
    dataset_id: str,
    *,
    columns: list[str] | None = None,
    constraints: dict[str, str] | None = None,
    output_path: Path | None = None,
    timeout: int = 300,
) -> Path:
    """Download a tabledap dataset as CSV.

    Parameters
    ----------
    server_url:
        Base ERDDAP URL.
    dataset_id:
        The ERDDAP dataset identifier.
    columns:
        Columns to download.  ``None`` downloads all.
    constraints:
        ERDDAP constraints (e.g. ``{"time>=": "2024-01-01"}``).
    output_path:
        Destination file.  If *None* a temporary file is created.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    Path to the downloaded CSV file.
    """
    base = server_url.rstrip("/")
    col_part = ",".join(columns) if columns else ""
    constraint_part = ""
    if constraints:
        constraint_part = "&" + "&".join(
            f"{urllib.parse.quote(k)}{urllib.parse.quote(v)}" for k, v in constraints.items()
        )
    url = f"{base}/tabledap/{urllib.parse.quote(dataset_id)}.csv?{col_part}{constraint_part}"

    text = _fetch_text(url, timeout=timeout)

    if output_path is None:
        fd, tmp = tempfile.mkstemp(suffix=".csv", prefix=f"erddap_{dataset_id}_")
        output_path = Path(tmp)
        os.close(fd)

    # Drop the ERDDAP units row (second line after header)
    lines = text.splitlines(keepends=True)
    if len(lines) >= 2:  # noqa: PLR2004
        lines = [lines[0]] + lines[2:]

    output_path.write_text("".join(lines), encoding="utf-8")
    return output_path


def read_erddap_csv(path: Path) -> pd.DataFrame:
    """Read a CSV previously downloaded via :func:`download_csv`.

    The file is expected to *not* have the ERDDAP units row
    (already stripped by ``download_csv``).
    """
    return pd.read_csv(path, low_memory=False)
