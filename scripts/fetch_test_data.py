#!/usr/bin/env python3
"""Download OceanStream test data from Azure Blob Storage.

Usage::

    # Download everything (provider CSV + large EK80/R2R data)
    python scripts/fetch_test_data.py

    # Download only the small provider CSV files (fast, <1 MB)
    python scripts/fetch_test_data.py --tier small

    # Download a specific category
    python scripts/fetch_test_data.py --tier large --category r2r

    # List available files without downloading
    python scripts/fetch_test_data.py --list

The data is stored in a public Azure Blob container and requires no
authentication.  Files are downloaded to ``raw_data/`` at the project
root and are skipped if they already exist (use ``--force`` to
re-download).

Tiers:
    small   Provider CSV test data (~88 KB total).
            Required for: integration tests.
    large   EK80 raw, R2R archives, Saildrone CSVs (~2.5 GB total).
            Required for: e2e tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "raw_data"
MANIFEST_PATH = Path(__file__).resolve().parent / "test_data_manifest.json"

# Public Azure Blob Storage base URL (no auth required)
BLOB_BASE_URL = "https://oceanstreamtestdata.blob.core.windows.net/test-data"


def load_manifest() -> dict:
    """Load the test data manifest."""
    with MANIFEST_PATH.open() as f:
        return json.load(f)


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path, expected_sha256: str | None = None) -> bool:
    """Download a file from URL to dest. Returns True if downloaded."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading {dest.relative_to(PROJECT_ROOT)} ...", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)  # noqa: S310 — trusted URL
    except Exception as e:
        print(f"FAILED: {e}")
        return False

    if expected_sha256:
        actual = sha256_file(dest)
        if actual != expected_sha256:
            print(f"CHECKSUM MISMATCH (expected {expected_sha256[:12]}..., got {actual[:12]}...)")
            dest.unlink()
            return False

    print("OK")
    return True


def extract_archive(archive_path: Path, extract_to: Path) -> bool:
    """Extract a .tar.gz archive safely, then remove the archive."""
    print(f"  Extracting to {extract_to.relative_to(PROJECT_ROOT)}/ ...", end=" ", flush=True)
    try:
        extract_to.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "r:gz") as tf:
            # Security: reject paths with .. or absolute components
            for member in tf.getmembers():
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    print(f"REFUSED: unsafe path in archive: {member.name}")
                    return False
            tf.extractall(path=extract_to, filter="data")  # noqa: S202
        archive_path.unlink()
        print("OK")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False


def list_files(manifest: dict, tier: str | None, category: str | None) -> None:
    """Print available files."""
    for entry in manifest["files"]:
        if tier and entry["tier"] != tier:
            continue
        if category and entry["category"] != category:
            continue
        size = entry.get("size_bytes") or 0
        if size > 1_000_000:
            size_str = f"{size / 1_000_000:.1f} MB"
        elif size > 1_000:
            size_str = f"{size / 1_000:.1f} KB"
        else:
            size_str = f"{size} B"
        # For archives, check if the extracted directory exists
        extract_to = entry.get("extract_to")
        if extract_to:
            target = RAW_DATA_DIR / extract_to
        else:
            target = RAW_DATA_DIR / entry["path"]
        status = "EXISTS" if target.exists() else "MISSING"
        print(f"  [{entry['tier']:5s}] [{status:7s}] {size_str:>10s}  {entry['path']}")


def fetch(
    tier: str | None = None,
    category: str | None = None,
    force: bool = False,
) -> tuple[int, int]:
    """Download test data files. Returns (downloaded, skipped) counts."""
    manifest = load_manifest()
    downloaded = 0
    skipped = 0

    for entry in manifest["files"]:
        if tier and entry["tier"] != tier:
            continue
        if category and entry["category"] != category:
            continue

        dest = RAW_DATA_DIR / entry["path"]
        extract_to = entry.get("extract_to")

        # For archives, check if extracted directory already exists
        if extract_to:
            target = RAW_DATA_DIR / extract_to
            if target.exists() and not force:
                skipped += 1
                continue
        elif dest.exists() and not force:
            skipped += 1
            continue

        url = f"{BLOB_BASE_URL}/{entry['path']}"
        if download_file(url, dest, entry.get("sha256")):
            if extract_to:
                extract_archive(dest, RAW_DATA_DIR / extract_to)
            downloaded += 1

    return downloaded, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download OceanStream test data from Azure Blob Storage."
    )
    parser.add_argument(
        "--tier",
        choices=["small", "large"],
        default=None,
        help="Download only files in this tier (default: all).",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Download only files in this category (e.g. r2r, saildrone).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if files already exist.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List available files without downloading.",
    )
    args = parser.parse_args()

    manifest = load_manifest()

    if args.list_only:
        list_files(manifest, args.tier, args.category)
        return

    print(f"Downloading test data to {RAW_DATA_DIR}/")
    print()
    downloaded, skipped = fetch(args.tier, args.category, args.force)
    print()
    print(f"Done: {downloaded} downloaded, {skipped} skipped (already present).")
    if downloaded == 0 and skipped > 0:
        print("All files already present. Use --force to re-download.")


if __name__ == "__main__":
    main()
