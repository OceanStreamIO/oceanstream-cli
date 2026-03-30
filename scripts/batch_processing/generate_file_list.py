#!/usr/bin/env python3
"""Generate the Zarr file list from Azure Blob Storage for fast discovery.

Uses month-based prefix queries to avoid the 5000-blob pagination limit.
Optionally enriches with file_freqs from the PostgreSQL database.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict


def _list_blobs_by_prefix(container: str, prefix: str, conn_str: str) -> list[str]:
    """List blob names under a prefix using az CLI with --num-results * for full listing."""
    result = subprocess.run(
        [
            "az", "storage", "blob", "list",
            "--container-name", container,
            "--connection-string", conn_str,
            "--prefix", prefix,
            "--num-results", "*",
            "--query", "[].name",
            "-o", "json",
        ],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"  WARNING: az CLI failed for prefix {prefix}: {result.stderr}", file=sys.stderr)
        return []
    return json.loads(result.stdout)


def _load_freqs_from_db() -> dict[str, str]:
    """Load file_freqs mapping from PostgreSQL (file_name → file_freqs).

    Returns empty dict if DB is unreachable.
    """
    try:
        import psycopg2
    except ImportError:
        print("  psycopg2 not installed — skipping DB enrichment", file=sys.stderr)
        return {}

    db_host = os.environ.get("DB_HOST", "4.209.38.254")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "oceanstream")
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ.get("DB_PASSWORD")

    if not db_password:
        print("  DB_PASSWORD not set — skipping DB enrichment", file=sys.stderr)
        return {}

    try:
        conn = psycopg2.connect(
            host=db_host, port=db_port, dbname=db_name,
            user=db_user, password=db_password,
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT f.file_name, f.file_freqs
            FROM files f
            JOIN surveys s ON f.survey_db_id = s.id
            WHERE s.cruise_id = %s
        """, (cruise_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        freq_map = {row[0]: row[1] for row in rows if row[1]}
        print(f"  Loaded {len(freq_map)} file_freqs entries from DB")
        return freq_map
    except Exception as e:
        print(f"  DB query failed: {e} — skipping DB enrichment", file=sys.stderr)
        return {}


def generate_file_list(
    container: str = "processed",
    cruise_id: str = "SD_TPOS2023_v03",
    output_file: str = "file_list.json",
    enrich_from_db: bool = True,
):
    """Scan Azure blob storage and generate a JSON file list.

    Uses month-based prefix queries (2023-05 through 2023-11) to avoid
    the default 5000-blob pagination limit of `az storage blob list`.
    """
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("AZ_SOURCE_CONNECTION_STRING")
    if not conn_str:
        print("ERROR: Set AZURE_STORAGE_CONNECTION_STRING or AZ_SOURCE_CONNECTION_STRING", file=sys.stderr)
        sys.exit(1)

    # Query by month prefix to avoid pagination limits
    # Filename pattern: SD_TPOS2023_v03-Phase0-D{YYYYMMDD}-T{HHMMSS}-0
    months = ["202305", "202306", "202307", "202308", "202309", "202310", "202311"]
    all_blobs: list[str] = []

    for month in months:
        prefix = f"{cruise_id}/{cruise_id}-Phase0-D{month}"
        print(f"Querying prefix: {prefix}")
        blobs = _list_blobs_by_prefix(container, prefix, conn_str)
        print(f"  → {len(blobs)} blobs")
        all_blobs.extend(blobs)

    print(f"Total blobs: {len(all_blobs)}")

    # Find .zgroup entries to identify Zarr stores
    zarr_stores = set()
    for blob in all_blobs:
        if blob.endswith("/.zgroup"):
            store_path = blob.rsplit("/.zgroup", 1)[0]
            zarr_stores.add(store_path)

    # Filter: nested non-denoised only (cruise_id/file_name/file_name.zarr)
    filtered = []
    for store in sorted(zarr_stores):
        parts = store.split("/")
        if (
            len(parts) == 3
            and parts[2].endswith(".zarr")
            and parts[1] == parts[2].replace(".zarr", "")
            and "_denoised" not in parts[2]
        ):
            filtered.append(store)

    print(f"Found {len(zarr_stores)} total Zarr stores, {len(filtered)} after filtering")

    # Optionally load file_freqs from database
    freq_map: dict[str, str] = {}
    if enrich_from_db:
        freq_map = _load_freqs_from_db()

    # Extract metadata
    records = []
    for store_path in filtered:
        parts = store_path.split("/")
        file_name = parts[1]

        # Extract datetime from filename: ...-D{YYYYMMDD}-T{HHMMSS}-...
        dt_match = re.search(r"D(\d{4})(\d{2})(\d{2})-T(\d{2})(\d{2})(\d{2})", file_name)
        dt_str = None
        if dt_match:
            y, m, d, h, mi, s = dt_match.groups()
            dt_str = f"{y}-{m}-{d}T{h}:{mi}:{s}"

        records.append({
            "zarr_path": store_path,
            "file_name": file_name,
            "file_start_time": dt_str,
            "file_freqs": freq_map.get(file_name),
            "location_data": [],
        })

    # Sort by start time
    records.sort(key=lambda r: r["file_start_time"] or "")

    with open(output_file, "w") as f:
        json.dump(records, f, indent=2)

    # Summary
    by_month: dict[str, int] = defaultdict(int)
    by_freq: dict[str, int] = defaultdict(int)
    for rec in records:
        dt = rec["file_start_time"]
        if dt:
            by_month[dt[:7]] += 1
        by_freq[rec["file_freqs"] or "unknown"] += 1

    print(f"\nWrote {len(records)} records to {output_file}")
    print("By month:")
    for month in sorted(by_month):
        print(f"  {month}: {by_month[month]} files")
    print("By frequency:")
    for freq, count in sorted(by_freq.items()):
        print(f"  {freq}: {count} files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate file list from Azure Blob Storage")
    parser.add_argument("--container", default="processed")
    parser.add_argument("--cruise-id", default="SD_TPOS2023_v03")
    parser.add_argument("--output", default="file_list.json")
    parser.add_argument("--no-db", action="store_true", help="Skip database enrichment")
    args = parser.parse_args()
    generate_file_list(args.container, args.cruise_id, args.output, enrich_from_db=not args.no_db)
