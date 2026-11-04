"""Copy a campaign's raw files into the project bucket, where the cluster is staged from.

Cluster transfer nodes reach object storage (through this side, over SSH) but
not Azure Files or arbitrary web servers, so raw input is mirrored to an S3
prefix first. Every mirror is idempotent: objects already there at the same
size are skipped, so an interrupted mirror resumes.

- :func:`mirror_azure_raw` / :func:`mirror_azure_gps`: the Saildrone file share
  and GPS blobs (the TPOS route), with the pipeline's own file discovery.
- :func:`mirror_archive`: HTTPS ``.tar.gz``/``.tar``/``.zip`` archives (R2R BagIt
  tarballs, Zenodo zips) streamed member by member, keeping only raw files.
- :func:`mirror_files`: plain HTTPS files (Zenodo records, NMDC).
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import IO, Any

logger = logging.getLogger(__name__)

RAW_PATTERNS = ("*.raw", "*.idx")
PART = 64 << 20


def s3_endpoint() -> str:
    ep = (os.environ.get("OCEANSTREAM_S3_ENDPOINT") or os.environ.get("AWS_S3_ENDPOINT")
          or os.environ.get("S3_ENDPOINT_URL") or "")
    if ep and not ep.startswith(("http://", "https://")):
        ep = f"https://{ep}"
    return ep


def s3_client() -> Any:
    import boto3  # noqa: PLC0415 - optional dependency

    return boto3.client("s3", endpoint_url=s3_endpoint() or None)


def existing_sizes(s3: Any, bucket: str, prefix: str) -> dict[str, int]:
    """Key -> size for every object under *prefix*."""
    out: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = int(obj["Size"])
    return out


def upload_stream(s3: Any, stream: IO[bytes], bucket: str, key: str, part: int = PART) -> int:
    """Upload a non-seekable stream, in parts once it outgrows one; returns its size."""
    chunk = stream.read(part)
    if len(chunk) < part:
        s3.put_object(Bucket=bucket, Key=key, Body=chunk)
        return len(chunk)
    upload = s3.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
    parts, size, number = [], 0, 1
    try:
        while chunk:
            tag = s3.upload_part(Bucket=bucket, Key=key, PartNumber=number, UploadId=upload,
                                 Body=chunk)["ETag"]
            parts.append({"PartNumber": number, "ETag": tag})
            size += len(chunk)
            number += 1
            chunk = stream.read(part)
        s3.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload,
                                     MultipartUpload={"Parts": parts})
    except BaseException:
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)
        raise
    return size


def wanted(name: str, patterns: Iterable[str] = RAW_PATTERNS) -> bool:
    base = PurePosixPath(name).name
    return any(fnmatch(base, p) for p in patterns)


# -- HTTPS archives and files -------------------------------------------------------------------
@dataclass
class MirrorResult:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0


def _open_url(url: str, timeout: float = 120) -> IO[bytes]:
    import requests  # noqa: PLC0415 - optional dependency

    r = requests.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    r.raw.decode_content = True
    return r.raw  # type: ignore[no-any-return]


def tar_members(stream: IO[bytes]) -> Iterator[tuple[str, int, IO[bytes]]]:
    """(name, size, file) for each regular file of a tar or tar.gz stream, in order."""
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            fh = tar.extractfile(member)
            if fh is not None:
                yield member.name, member.size, fh


def zip_members(stream: IO[bytes]) -> Iterator[tuple[str, int, IO[bytes]]]:
    """(name, size, file) for each file of a zip. A zip's index is at its end, so the
    archive is spooled to a temporary file first."""
    with tempfile.TemporaryFile() as tmp:
        while chunk := stream.read(8 << 20):
            tmp.write(chunk)
        tmp.seek(0)
        with zipfile.ZipFile(tmp) as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    with zf.open(info) as fh:
                        yield info.filename, info.file_size, fh


def mirror_archive(  # noqa: PLR0913
    url: str, bucket: str, prefix: str, *, patterns: Iterable[str] = RAW_PATTERNS,
    flatten: bool = True, opener: Callable[[str], IO[bytes]] = _open_url, s3: Any = None,
    dry_run: bool = False,
) -> MirrorResult:
    """Stream an archive from *url* and upload its raw members under ``s3://bucket/prefix/``.

    The archive is read once, front to back, with nothing written locally except
    a zip, which must be spooled because its index is at the end. *flatten* keeps
    only base names (R2R BagIt nests files under ``data/…``). Members already in
    the bucket at their size are read past, not uploaded again.
    """
    s3 = s3 or s3_client()
    prefix = prefix.strip("/")
    have = existing_sizes(s3, bucket, prefix + "/")
    result = MirrorResult()
    stream = opener(url)
    members = zip_members(stream) if url.lower().split("?")[0].endswith(".zip") else (
        tar_members(stream))
    for name, size, fh in members:
        if not wanted(name, patterns):
            continue
        rel = PurePosixPath(name).name if flatten else name.lstrip("./")
        key = f"{prefix}/{rel}"
        if have.get(key) == size:
            result.skipped += 1
            continue
        if dry_run:
            logger.info("would copy %s (%.1f MB)", key, size / 1e6)
            result.copied += 1
            continue
        try:
            got = upload_stream(s3, fh, bucket, key)
            if got != size:
                raise RuntimeError(f"{key}: uploaded {got} bytes of {size}")
            result.copied += 1
            result.bytes += got
            logger.info("copied %8.1f MB  %s", size / 1e6, key)
        except Exception as e:  # noqa: BLE001 - report all at the end
            result.failed += 1
            logger.error("FAILED %s: %s", key, e)
    return result


def mirror_files(  # noqa: PLR0913
    urls: dict[str, str], bucket: str, prefix: str, *, sizes: dict[str, int] | None = None,
    opener: Callable[[str], IO[bytes]] = _open_url, s3: Any = None, workers: int = 4,
    dry_run: bool = False,
) -> MirrorResult:
    """Copy ``name -> url`` files to ``s3://bucket/prefix/<name>``.

    Zips are unpacked on the way (only raw members are kept); other files are
    copied as they are. *sizes* (e.g. from a Zenodo record) lets files already
    mirrored be skipped without opening them.
    """
    s3 = s3 or s3_client()
    prefix = prefix.strip("/")
    have = existing_sizes(s3, bucket, prefix + "/")
    sizes = sizes or {}
    result = MirrorResult()

    def one(name: str, url: str) -> MirrorResult:
        if name.lower().endswith(".zip"):
            return mirror_archive(url, bucket, prefix, opener=opener, s3=s3, dry_run=dry_run)
        key = f"{prefix}/{name}"
        if key in have and (name not in sizes or have[key] == sizes[name]):
            return MirrorResult(skipped=1)
        if dry_run:
            return MirrorResult(copied=1)
        got = upload_stream(s3, opener(url), bucket, key)
        if name in sizes and got != sizes[name]:
            raise RuntimeError(f"{key}: uploaded {got} bytes of {sizes[name]}")
        logger.info("copied %8.1f MB  %s", got / 1e6, key)
        return MirrorResult(copied=1, bytes=got)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, n, u): n for n, u in urls.items()}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001 - report all at the end
                logger.error("FAILED %s: %s", futures[fut], e)
                r = MirrorResult(failed=1)
            result.copied += r.copied
            result.skipped += r.skipped
            result.failed += r.failed
            result.bytes += r.bytes
    return result


def zenodo_files(record: str, timeout: float = 60) -> dict[str, tuple[str, int]]:
    """``name -> (download URL, size)`` for the files of a Zenodo record (id or DOI)."""
    import requests  # noqa: PLC0415 - optional dependency

    rid = record.rsplit("zenodo.", 1)[-1].strip("/")
    r = requests.get(f"https://zenodo.org/api/records/{rid}", timeout=timeout)
    r.raise_for_status()
    out = {}
    for f in r.json().get("files", []):
        name = f.get("key") or f.get("filename")
        url = (f.get("links") or {}).get("self") or (
            f"https://zenodo.org/records/{rid}/files/{name}?download=1")
        out[name] = (url, int(f.get("size", 0)))
    return out


# -- Azure (Saildrone file share) -----------------------------------------------------------------
def _pipeline_dir(explicit: str = "") -> Path:
    candidates = [Path(explicit)] if explicit else []
    candidates.append(Path(__file__).resolve().parents[2] / "scripts" / "batch_processing")
    for c in candidates:
        if (c / "process_from_raw.py").is_file():
            return c
    raise FileNotFoundError(f"process_from_raw.py not found in {[str(c) for c in candidates]}")


def mirror_azure_raw(  # noqa: PLR0913
    cruise_id: str, start_date: str, end_date: str, bucket: str, *, prefix: str = "hpc/raw",
    file_share: str = "saildroneraw", file_share_path: str = "DATA", workers: int = 4,
    pipeline_dir: str = "", dry_run: bool = False,
) -> MirrorResult:
    """Raw files of a file-share cruise to ``<prefix>/<cruise>/<day>/``.

    Files are chosen with the pipeline's own ``discover_raw_files``, so the mirror
    holds exactly what a file-share run would process. Needs
    ``AZURE_STORAGE_CONNECTION_STRING``.
    """
    from azure.storage.fileshare import ShareServiceClient  # noqa: PLC0415

    sys.path.insert(0, str(_pipeline_dir(pipeline_dir)))
    argv, sys.argv = sys.argv, [
        "process_from_raw.py", "--cruise-id", cruise_id, "--start-date", start_date,
        "--end-date", end_date, "--file-share", file_share, "--file-share-path", file_share_path,
        "--raw-source", "fileshare"]
    try:
        import process_from_raw  # noqa: PLC0415 - the pipeline script

        files = process_from_raw.discover_raw_files(process_from_raw.parse_args())
    finally:
        sys.argv = argv
    if not files:
        raise FileNotFoundError(f"No raw files found for {start_date}..{end_date}")
    s3 = s3_client()
    share = ShareServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]).get_share_client(file_share)

    def copy_one(name: str, record: dict[str, Any]) -> MirrorResult:
        day = (record.get("file_start_time") or "")[:10] or "undated"
        key = f"{prefix.strip('/')}/{cruise_id}/{day}/{Path(name).name}"
        fc = share.get_file_client(f"{file_share_path}/{name}")
        size = fc.get_file_properties().size
        try:
            if s3.head_object(Bucket=bucket, Key=key)["ContentLength"] == size:
                return MirrorResult(skipped=1)
        except s3.exceptions.ClientError:
            pass
        if dry_run:
            return MirrorResult(copied=1)
        # Spool through a temp file: older SDKs' downloaders are not file-like.
        with tempfile.TemporaryFile() as tmp:
            fc.download_file(max_concurrency=4).readinto(tmp)
            tmp.seek(0)
            s3.upload_fileobj(tmp, bucket, key)
        logger.info("copied %8.1f MB  %s", size / 1e6, key)
        return MirrorResult(copied=1, bytes=size)

    result = MirrorResult()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(copy_one, n, r): n for n, r in files}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001 - keep going; report at the end
                logger.error("FAILED %s: %s", futures[fut], e)
                r = MirrorResult(failed=1)
            result.copied += r.copied
            result.skipped += r.skipped
            result.failed += r.failed
            result.bytes += r.bytes
    return result


def mirror_azure_gps(cruise_id: str, container: str, bucket: str, *,
                     prefix: str = "hpc/gps", dry_run: bool = False) -> MirrorResult:
    """GPS GeoParquet blobs ``<container>/<cruise>/**`` to ``<prefix>/<cruise>/…``."""
    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

    s3 = s3_client()
    cc = BlobServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]).get_container_client(container)
    blobs = [b for b in cc.list_blobs(name_starts_with=f"{cruise_id}/")
             if b.name.endswith((".parquet", ".geoparquet"))]
    if not blobs:
        raise FileNotFoundError(f"No GPS parquet under {container}/{cruise_id}/")
    result = MirrorResult()
    for b in blobs:
        key = f"{prefix.strip('/')}/{b.name}"
        try:
            if s3.head_object(Bucket=bucket, Key=key)["ContentLength"] == b.size:
                result.skipped += 1
                continue
        except s3.exceptions.ClientError:
            pass
        if dry_run:
            result.copied += 1
            continue
        try:
            buf = io.BytesIO()
            cc.download_blob(b.name).readinto(buf)
            buf.seek(0)
            s3.upload_fileobj(buf, bucket, key)
            result.copied += 1
        except Exception as e:  # noqa: BLE001
            result.failed += 1
            logger.error("FAILED %s: %s", b.name, e)
    return result
