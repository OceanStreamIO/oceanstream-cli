"""S3-compatible object storage backend.

Drop-in replacements for ``oceanstream.echodata.storage`` functions so the
batch pipeline writes every zarr store and PNG to an S3 endpoint (CloudFerro,
MinIO, AWS) instead of Azure Blob.

Built on echopype's own cloud convention rather than a bespoke filesystem
wrapper: a ``storage_options`` dict alongside a ``s3://`` URI, and
``fsspec.get_mapper(uri, **storage_options)`` to obtain a store or filesystem
(see ``echopype/utils/io.py``). Every function here resolves to a URI and
passes ``storage_options`` as a dict — echopype itself also has an
expanded-kwargs style in ``open_source``; that one is not copied.

Usage — call ``patch_storage(bucket, prefix, ...)`` **before** importing
``process_campaign`` or any module that does
``from oceanstream.echodata.storage import ...``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import fsspec
import xarray as xr

logger = logging.getLogger(__name__)

#: fsspec storage options in echopype's exact shape — see
#: ``echopype/tests/conftest.py`` (``minio_bucket`` fixture).
_STORAGE_OPTIONS: dict[str, Any] = {}
_BUCKET: str = ""
_PREFIX: str = ""


# ── configuration helpers ────────────────────────────────────────────────

def _root() -> str:
    """Return the ``s3://`` URI that every container path is resolved against."""
    if not _BUCKET:
        raise RuntimeError(
            "S3 storage is not configured — call s3_storage.patch_storage() first."
        )
    return f"s3://{_BUCKET}/{_PREFIX}" if _PREFIX else f"s3://{_BUCKET}"


def _resolve(path: str, container: Optional[str] = None) -> str:
    """Resolve *path* inside *container* to ``s3://{bucket}/{prefix}/{container}/{path}``."""
    parts = [_root()]
    if container:
        parts.append(str(container).strip("/"))
    if path:
        parts.append(str(path).strip("/"))
    return "/".join(parts)


def storage_options() -> dict[str, Any]:
    """Return a copy of the fsspec storage options for the configured endpoint."""
    return dict(_STORAGE_OPTIONS)


def _mapper(uri: str) -> fsspec.FSMap:
    return fsspec.get_mapper(uri, **_STORAGE_OPTIONS)


def _filesystem():
    """Return the raw s3fs filesystem, obtained the way echopype obtains one."""
    return _mapper(_root()).fs


def _normalise_endpoint(endpoint: str | None) -> str | None:
    """Prepend a scheme when the endpoint is given as a bare host (EDITO does)."""
    if not endpoint:
        return None
    return endpoint if "://" in endpoint else f"https://{endpoint}"


def _build_storage_options(
    endpoint_url: str | None,
    key: str | None,
    secret: str | None,
    region: str | None,
) -> dict[str, Any]:
    """Assemble the echopype-shaped storage options, falling back to the env."""
    endpoint_url = _normalise_endpoint(
        endpoint_url
        or os.environ.get("AWS_S3_ENDPOINT")
        or os.environ.get("S3_ENDPOINT_URL")
    )
    key = key or os.environ.get("AWS_ACCESS_KEY_ID")
    secret = secret or os.environ.get("AWS_SECRET_ACCESS_KEY")
    region = region or os.environ.get("AWS_DEFAULT_REGION")

    opts: dict[str, Any] = {}
    client_kwargs: dict[str, Any] = {}
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url
    if region:
        client_kwargs["region_name"] = region
    if client_kwargs:
        opts["client_kwargs"] = client_kwargs
    # Omitting key/secret leaves botocore's default credential chain in charge.
    if key:
        opts["key"] = key
    if secret:
        opts["secret"] = secret
    return opts


# ── replacement functions ────────────────────────────────────────────────

def save_dataset_to_azure(
    dataset: xr.Dataset,
    zarr_path: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> str:
    """Save dataset to an S3 zarr store (replaces the Azure version).

    The existing store is removed before writing: ``mode="w"`` leaves orphaned
    chunk keys behind on object storage, which a later read would silently mix
    into the new dataset.
    """
    from oceanstream.echodata.utils.encoding import fix_chunking

    uri = _resolve(zarr_path, container)
    mapper = _mapper(uri)
    fs = mapper.fs

    logger.info("Saving dataset to S3: %s", uri)
    dataset = fix_chunking(dataset)

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            if fs.exists(mapper.root):
                fs.rm(mapper.root, recursive=True)
                fs.invalidate_cache(mapper.root)
            dataset.to_zarr(uri, mode="w-", storage_options=_STORAGE_OPTIONS)
            return uri
        except Exception as e:  # object-storage failures are not a fixed set
            if attempt == max_attempts:
                logger.error(
                    "to_zarr failed for %s after %d attempts: %s", uri, attempt, e
                )
                raise
            logger.warning(
                "to_zarr attempt %d/%d failed for %s: %s — retrying",
                attempt, max_attempts, uri, e,
            )
            time.sleep(1.0)
    return uri


def open_sv_from_azure(
    campaign_id: str | None = None,
    filename: str | None = None,
    container: Optional[str] = None,
    chunks: Optional[dict] = None,
    connection_string: str | None = None,
    *,
    zarr_path: str | None = None,
) -> xr.Dataset:
    """Open a zarr store from S3 (replaces the Azure version).

    Keeps the original dual-mode signature: either ``campaign_id`` + ``filename``
    or the keyword-only ``zarr_path``.
    """
    if zarr_path is not None:
        path = zarr_path
    elif campaign_id is not None and filename is not None:
        from oceanstream.echodata.storage import build_echodata_path

        path = build_echodata_path(campaign_id, f"{filename}_Sv", stage="calibrated")
    else:
        raise ValueError(
            "open_sv_from_azure needs either zarr_path=... (keyword-only) or both "
            "campaign_id and filename. Passing a zarr path positionally binds it to "
            "campaign_id — that only works against the local backend, whose first "
            "positional parameter drifted from the original."
        )

    uri = _resolve(path, container)
    logger.info("Opening Sv from S3: %s", uri)

    open_kw: dict = {"storage_options": _STORAGE_OPTIONS}
    if chunks:
        open_kw["chunks"] = chunks

    ds = xr.open_zarr(uri, **open_kw)
    if not ds.data_vars:
        # Zarr v3 stores lack consolidated metadata — retry without it
        ds = xr.open_zarr(uri, consolidated=False, **open_kw)

    # Masked-Sv and pruned-view stores come back as the Sv they stand for.
    from oceanstream.echodata.products import resolve_product

    return resolve_product(
        ds,
        lambda p, c: open_sv_from_azure(zarr_path=p, container=c or container, chunks=chunks),
    )


def get_azure_zarr_store(
    path: str,
    container: Optional[str] = None,
    mode: str = "w",
    connection_string: str | None = None,
) -> fsspec.FSMap:
    """Return an ``fsspec.FSMap`` for a zarr path (matches the Azure original).

    The campaign-aggregation path in ``process_campaign`` feeds the result
    straight to ``ds.to_zarr(store, ...)``, so this must be a mapper and not a
    URI string.
    """
    return _mapper(_resolve(path, container))


def get_azure_filesystem(connection_string: str | None = None) -> "_S3PrefixFS":
    """Return a filesystem view rooted at the configured bucket + prefix.

    The underlying s3fs instance is obtained the echopype way
    (``fsspec.get_mapper(uri, **storage_options).fs``) but is wrapped, because
    every call site addresses blobs as ``{container}/{path}`` — an Azure
    convention that a bare s3fs filesystem would read as ``{bucket}/{key}``.
    """
    return _S3PrefixFS(_filesystem(), _BUCKET, _PREFIX)


def ensure_container_exists(
    container_name: str,
    connection_string: str | None = None,
    public_access: str | None = None,
) -> None:
    """Create the target bucket if missing. Prefixes need no creation on S3.

    *public_access* has no S3 equivalent that is safe to set implicitly and is
    ignored; bucket policies are managed out of band.
    """
    fs = _filesystem()
    try:
        if fs.exists(_BUCKET):
            logger.debug("Bucket '%s' already exists", _BUCKET)
            return
        logger.info("Creating bucket '%s'", _BUCKET)
        fs.mkdir(_BUCKET)
    except Exception as e:
        # A deployment key often lacks CreateBucket while the bucket already
        # exists; the first write will fail loudly if it genuinely does not.
        logger.warning("Could not verify/create bucket '%s': %s", _BUCKET, e)


def generate_container_name(cruise_id: str) -> str:
    """Generate a unique storage-safe container name from a cruise id."""
    import re
    import uuid
    from datetime import datetime as _dt

    date_str = _dt.now().strftime("%Y%m%d")
    unique_id = uuid.uuid4().hex[:8]
    raw_name = f"{cruise_id}{date_str}{unique_id}".lower()
    sanitized = re.sub(r"[^a-z0-9-]", "", raw_name)
    return sanitized[:63]


def upload_file_to_blob(
    local_path: str,
    blob_path: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
    *,
    container_name: Optional[str] = None,
) -> None:
    """Upload a local file to S3 (replaces the Azure upload).

    Accepts both the original ``container_name`` keyword and the ``container``
    keyword used by the batch pipeline.
    """
    target = _resolve(blob_path, container or container_name)
    _filesystem().put(str(local_path), target)
    logger.info("Uploaded %s → %s", Path(local_path).name, target)


def get_zarr_store_uri(
    path: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> str:
    return _resolve(path, container)


# ── helpers ──────────────────────────────────────────────────────────────

class _S3PrefixFS:
    """Filesystem view rooted at ``{bucket}/{prefix}`` taking container-relative paths.

    Mirrors ``local_storage._LocalFS``: ``ls()`` returns paths relative to the
    root so callers can strip a leading ``{container}/`` exactly as they do
    against Azure.
    """

    def __init__(self, fs, bucket: str, prefix: str) -> None:
        self.fs = fs
        self.root = "/".join(p for p in (bucket, prefix.strip("/")) if p)

    def _abs(self, path: str) -> str:
        path = str(path).strip("/")
        for scheme in ("s3://", "s3a://"):
            if path.startswith(scheme):
                return path[len(scheme):]
        return f"{self.root}/{path}" if path else self.root

    def _rel(self, path: str) -> str:
        path = str(path)
        for scheme in ("s3://", "s3a://"):
            if path.startswith(scheme):
                path = path[len(scheme):]
                break
        prefix = f"{self.root}/"
        return path[len(prefix):] if path.startswith(prefix) else path

    def ls(self, path: str, detail: bool = False, **kwargs) -> list:
        try:
            entries = self.fs.ls(self._abs(path), detail=detail, **kwargs)
        except FileNotFoundError:
            return []
        if not detail:
            return [self._rel(e) for e in entries]
        out = []
        for entry in entries:
            item = dict(entry)
            item["name"] = self._rel(item["name"])
            out.append(item)
        return out

    def find(self, path: str, **kwargs) -> list[str]:
        try:
            return [self._rel(p) for p in self.fs.find(self._abs(path), **kwargs)]
        except FileNotFoundError:
            return []

    def exists(self, path: str) -> bool:
        return self.fs.exists(self._abs(path))

    def isdir(self, path: str) -> bool:
        return self.fs.isdir(self._abs(path))

    def isfile(self, path: str) -> bool:
        return self.fs.isfile(self._abs(path))

    def rm(self, path: str, recursive: bool = False, **kwargs):
        return self.fs.rm(self._abs(path), recursive=recursive, **kwargs)

    def put(self, lpath, rpath, recursive: bool = False, **kwargs):
        kwargs.pop("overwrite", None)  # S3 PUT always overwrites
        return self.fs.put(str(lpath), self._abs(rpath), recursive=recursive, **kwargs)

    def get(self, rpath, lpath, recursive: bool = False, **kwargs):
        return self.fs.get(self._abs(rpath), str(lpath), recursive=recursive, **kwargs)

    def open(self, path: str, mode: str = "rb", **kwargs):
        return self.fs.open(self._abs(path), mode=mode, **kwargs)

    def get_mapper(self, path: str, **kwargs) -> fsspec.FSMap:
        return self.fs.get_mapper(self._abs(path), **kwargs)


# ── Dask worker plugin ───────────────────────────────────────────────────

try:  # distributed is only needed for the Dask path, not for patch_storage()
    from distributed.diagnostics.plugin import WorkerPlugin as _WorkerPlugin
except ImportError:
    _WorkerPlugin = object


class S3StoragePlugin(_WorkerPlugin):
    """Dask worker plugin that applies the S3 storage patches on each worker.

    Register with ``client.register_plugin(S3StoragePlugin(bucket, prefix))``.
    Credentials are deliberately not carried by default: leaving *key* and
    *secret* unset makes each worker read them from its own environment
    (a K8s secret) instead of shipping them through the scheduler.

    Subclassing ``WorkerPlugin`` is mandatory, not decorative: distributed
    2025.x onwards raises ``TypeError: Registering duck-typed plugins is not
    allowed`` for a plain class with a ``setup`` method.
    """

    name = "s3-storage"

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        endpoint_url: str | None = None,
        key: str | None = None,
        secret: str | None = None,
        region: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.endpoint_url = endpoint_url
        self.key = key
        self.secret = secret
        self.region = region

    def setup(self, worker) -> None:  # noqa: ARG002
        patch_storage(
            self.bucket,
            self.prefix,
            endpoint_url=self.endpoint_url,
            key=self.key,
            secret=self.secret,
            region=self.region,
        )


# ── monkey-patch entrypoint ──────────────────────────────────────────────

def patch_storage(
    bucket: str,
    prefix: str = "",
    *,
    endpoint_url: str | None = None,
    key: str | None = None,
    secret: str | None = None,
    region: str | None = None,
) -> None:
    """Replace ``oceanstream.echodata.storage`` functions with the S3 versions.

    Must be called **before** any downstream module imports storage functions
    at the module level. The pipeline modules use function-level imports, so
    calling this before ``run_pipeline`` is enough.
    """
    global _BUCKET, _PREFIX, _STORAGE_OPTIONS

    if not bucket:
        raise ValueError("An S3 bucket is required.")

    _BUCKET = bucket.strip("/")
    _PREFIX = prefix.strip("/")
    _STORAGE_OPTIONS = _build_storage_options(endpoint_url, key, secret, region)

    import oceanstream.echodata.storage as mod

    mod.save_dataset_to_azure = save_dataset_to_azure
    mod.open_sv_from_azure = open_sv_from_azure
    mod.get_azure_zarr_store = get_azure_zarr_store
    mod.get_azure_filesystem = get_azure_filesystem
    mod.ensure_container_exists = ensure_container_exists
    mod.generate_container_name = generate_container_name
    mod.upload_file_to_blob = upload_file_to_blob
    mod.get_zarr_store_uri = get_zarr_store_uri

    logger.info("Storage patched → S3 at %s", _root())
