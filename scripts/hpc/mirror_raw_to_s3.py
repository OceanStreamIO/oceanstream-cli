#!/usr/bin/env python3
"""Mirror raw EK80 files (and optionally GPS GeoParquet) from Azure to S3.

Cluster data-transfer nodes usually speak S3 but not the Azure Files protocol,
so raw input is copied to an S3 prefix first and staged onto the cluster from
there. File selection reuses the pipeline's own ``discover_raw_files`` so the
mirror holds exactly the files a fileshare run would process.

With ``--gps-container`` the cruise's GPS GeoParquet blobs
(``<container>/<cruise_id>/**``) are mirrored instead, to
``<gps-prefix>/<cruise_id>/`` with their partition paths kept; the pipeline
reads them on the cluster with ``--gps-dir``.

Idempotent: objects that already exist with the same size are skipped.

Environment:
    AZURE_STORAGE_CONNECTION_STRING   read access to the file share
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    AWS_S3_ENDPOINT or S3_ENDPOINT_URL (scheme optional)

Usage:
    python scripts/hpc/mirror_raw_to_s3.py --cruise-id SD_TPOS2023_v03 \\
        --start-date 2023-10-10 --end-date 2023-10-10 \\
        --bucket my-bucket --prefix hpc/raw
    python scripts/hpc/mirror_raw_to_s3.py --cruise-id SD_TPOS2023_v03 \\
        --gps-container gpsdata --bucket my-bucket --gps-prefix hpc/gps
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger("mirror_raw_to_s3")


def _pipeline_dir(explicit: str) -> Path:
    candidates = [Path(explicit)] if explicit else []
    candidates.append(Path(__file__).resolve().parents[1] / "batch_processing")
    for c in candidates:
        if (c / "process_from_raw.py").is_file():
            return c
    raise SystemExit(f"process_from_raw.py not found in {[str(c) for c in candidates]}")


def _endpoint() -> str:
    ep = os.environ.get("AWS_S3_ENDPOINT") or os.environ.get("S3_ENDPOINT_URL") or ""
    if ep and not ep.startswith(("http://", "https://")):
        ep = f"https://{ep}"
    return ep


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cruise-id", required=True)
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--gps-container", default="", help="Mirror GPS blobs from this container instead of raw files")
    p.add_argument("--gps-prefix", default="hpc/gps")
    p.add_argument("--file-share", default="saildroneraw")
    p.add_argument("--file-share-path", default="DATA")
    p.add_argument("--bucket", required=True)
    p.add_argument("--prefix", default="hpc/raw")
    p.add_argument("--pipeline-dir", default="", help="Directory holding process_from_raw.py")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # The Azure SDK logs every HTTP request at INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)

    if args.gps_container:
        return mirror_gps(args)
    if not (args.start_date and args.end_date):
        p.error("--start-date and --end-date are required for raw files")

    sys.path.insert(0, str(_pipeline_dir(args.pipeline_dir)))
    sys.argv = [
        "process_from_raw.py",
        "--cruise-id", args.cruise_id,
        "--start-date", args.start_date,
        "--end-date", args.end_date,
        "--file-share", args.file_share,
        "--file-share-path", args.file_share_path,
        "--raw-source", "fileshare",
    ]
    import process_from_raw  # noqa: E402

    cfg = process_from_raw.parse_args()
    files = process_from_raw.discover_raw_files(cfg)
    if not files:
        logger.error("No raw files found for %s..%s", args.start_date, args.end_date)
        return 1

    import boto3
    from azure.storage.fileshare import ShareServiceClient

    s3 = boto3.client("s3", endpoint_url=_endpoint() or None)
    share = ShareServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    ).get_share_client(args.file_share)

    def key_for(name: str, record: dict) -> str:
        day = (record.get("file_start_time") or "")[:10] or "undated"
        return f"{args.prefix.strip('/')}/{args.cruise_id}/{day}/{Path(name).name}"

    def copy_one(name: str, record: dict) -> tuple[str, int, str]:
        key = key_for(name, record)
        fc = share.get_file_client(f"{args.file_share_path}/{name}")
        size = fc.get_file_properties().size
        try:
            head = s3.head_object(Bucket=args.bucket, Key=key)
            if head["ContentLength"] == size:
                return key, size, "skipped"
        except s3.exceptions.ClientError:
            pass
        if args.dry_run:
            return key, size, "would copy"
        # Spool through a temp file: older SDKs' downloaders are not file-like.
        with tempfile.TemporaryFile() as tmp:
            fc.download_file(max_concurrency=4).readinto(tmp)
            tmp.seek(0)
            s3.upload_fileobj(tmp, args.bucket, key)
        return key, size, "copied"

    total = sum(r.get("file_size") or 0 for _, r in files)
    logger.info("%d file(s), %.2f GB -> s3://%s/%s/%s/",
                len(files), total / 1e9, args.bucket, args.prefix.strip("/"), args.cruise_id)

    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(copy_one, n, r): n for n, r in files}
        for fut in as_completed(futures):
            try:
                key, size, state = fut.result()
                logger.info("%-10s %8.1f MB  %s", state, size / 1e6, key)
            except Exception as exc:  # keep going; report at the end
                failed += 1
                logger.error("FAILED %s: %s", futures[fut], exc)

    if failed:
        logger.error("%d file(s) failed", failed)
        return 1
    logger.info("Done")
    return 0


def mirror_gps(args) -> int:
    import boto3
    from azure.storage.blob import BlobServiceClient

    s3 = boto3.client("s3", endpoint_url=_endpoint() or None)
    cc = BlobServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    ).get_container_client(args.gps_container)
    src_prefix = f"{args.cruise_id}/"
    blobs = [b for b in cc.list_blobs(name_starts_with=src_prefix)
             if b.name.endswith((".parquet", ".geoparquet"))]
    if not blobs:
        logger.error("No GPS parquet under %s/%s", args.gps_container, src_prefix)
        return 1
    logger.info("%d GPS file(s), %.1f MB", len(blobs), sum(b.size for b in blobs) / 1e6)

    failed = 0
    for b in blobs:
        key = f"{args.gps_prefix.strip('/')}/{b.name}"
        try:
            if s3.head_object(Bucket=args.bucket, Key=key)["ContentLength"] == b.size:
                continue
        except s3.exceptions.ClientError:
            pass
        if args.dry_run:
            logger.info("would copy %s", key)
            continue
        try:
            with tempfile.TemporaryFile() as tmp:
                cc.download_blob(b.name).readinto(tmp)
                tmp.seek(0)
                s3.upload_fileobj(tmp, args.bucket, key)
        except Exception as exc:
            failed += 1
            logger.error("FAILED %s: %s", b.name, exc)
    logger.info("Done (%d failed)", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
