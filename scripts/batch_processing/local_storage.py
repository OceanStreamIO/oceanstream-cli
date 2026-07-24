"""Local filesystem storage backend.

Drop-in replacements for ``oceanstream.echodata.storage`` functions
so the batch pipeline can save everything to local disk instead of Azure.

Usage — call ``patch_storage(output_root)`` **before** importing
``process_campaign`` or any module that does
``from oceanstream.echodata.storage import ...``.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import xarray as xr

logger = logging.getLogger(__name__)

# Will be set by patch_storage()
_OUTPUT_ROOT: Path = Path("/tmp/oceanstream/local_output")


# ── replacement functions ────────────────────────────────────────────────

def save_dataset_to_azure(
    dataset: xr.Dataset,
    zarr_path: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> str:
    """Save dataset to local zarr store (replaces Azure version)."""
    import dask
    from oceanstream.echodata.utils.encoding import fix_chunking

    dest = _resolve(zarr_path, container)
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Saving dataset locally: %s", dest)
    dataset = fix_chunking(dataset)
    # Use synchronous scheduler for local saves to avoid sending large
    # in-memory graphs through the distributed scheduler (1+ GiB overhead).
    with dask.config.set(scheduler="synchronous"):
        dataset.to_zarr(str(dest), mode="w")
    return str(dest)


_UNSET = object()  # sentinel to distinguish "not passed" from None


def open_sv_from_azure(
    zarr_path: str,
    container: Optional[str] = None,
    chunks: dict | None = _UNSET,
    connection_string: str | None = None,
    **kwargs,
) -> xr.Dataset:
    """Open a zarr store from local disk (replaces Azure version).

    Pass ``chunks=None`` explicitly for eager numpy-backed loading (no Dask).
    Omitting ``chunks`` defaults to ``"auto"`` (Dask-backed).
    """
    src = _resolve(zarr_path, container)
    logger.info("Opening local zarr: %s", src)
    if chunks is _UNSET:
        return xr.open_zarr(str(src), chunks={}, **kwargs)
    return xr.open_zarr(str(src), chunks=chunks, **kwargs)


def get_azure_zarr_store(
    zarr_path: str,
    container: Optional[str] = None,
    mode: str = "r",
    connection_string: str | None = None,
):
    """Return a local path string (replaces Azure FSMap store)."""
    dest = _resolve(zarr_path, container)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return str(dest)


def get_azure_filesystem():
    """Return a local filesystem that mimics fsspec interface for ls()."""
    return _LocalFS()


def ensure_container_exists(
    container: str,
    public_access: str = "container",
    connection_string: str | None = None,
) -> None:
    """Create the local output directory (replaces Azure container creation)."""
    dest = _OUTPUT_ROOT / container
    dest.mkdir(parents=True, exist_ok=True)
    logger.info("Local output directory: %s", dest)


def generate_container_name(cruise_id: str) -> str:
    return f"local-{cruise_id.lower()}"


def upload_file_to_blob(
    local_path: str,
    blob_name: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> None:
    """Copy a file to the local output tree (replaces Azure upload)."""
    dest = _resolve(blob_name, container)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_path, dest)
    logger.info("Copied %s → %s", Path(local_path).name, dest)


def get_zarr_store_uri(
    zarr_path: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> str:
    return str(_resolve(zarr_path, container))


# ── helpers ──────────────────────────────────────────────────────────────

def _resolve(zarr_path: str, container: Optional[str]) -> Path:
    """Resolve a zarr path to a local absolute path."""
    if container:
        return _OUTPUT_ROOT / container / zarr_path
    return _OUTPUT_ROOT / zarr_path


class _LocalFS:
    """Minimal fsspec-like filesystem for local listing (used by _reconstruct_day_zarrs)."""

    def ls(self, path: str, detail: bool = False) -> list[str]:
        local = _OUTPUT_ROOT / path
        if not local.exists():
            return []
        items = []
        for p in sorted(local.iterdir()):
            # Return path relative to _OUTPUT_ROOT (matches Azure's container/... format)
            rel = str(p.relative_to(_OUTPUT_ROOT))
            items.append(rel)
        return items


# ── Dask worker plugin ───────────────────────────────────────────────────

class LocalStoragePlugin:
    """Dask worker plugin that applies local storage patches on each worker.

    Register with ``client.register_worker_plugin(LocalStoragePlugin(root))``
    so that Dask workers use local filesystem instead of Azure.
    """

    name = "local-storage"

    def __init__(self, output_root: Path | str) -> None:
        self.output_root = str(output_root)

    def setup(self, worker) -> None:  # noqa: ARG002
        patch_storage(self.output_root)


# ── monkey-patch entrypoint ──────────────────────────────────────────────

def patch_storage(output_root: Path | str) -> None:
    """Replace ``oceanstream.echodata.storage`` functions with local versions.

    Must be called **before** any downstream module imports storage functions
    at the module level.  The pipeline modules (process_campaign.py, etc.)
    use function-level imports so this works if called before ``run_pipeline``.
    """
    global _OUTPUT_ROOT
    _OUTPUT_ROOT = Path(output_root)
    _OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    import oceanstream.echodata.storage as mod

    mod.save_dataset_to_azure = save_dataset_to_azure
    mod.open_sv_from_azure = open_sv_from_azure
    mod.get_azure_zarr_store = get_azure_zarr_store
    mod.get_azure_filesystem = get_azure_filesystem
    mod.ensure_container_exists = ensure_container_exists
    mod.generate_container_name = generate_container_name
    mod.upload_file_to_blob = upload_file_to_blob
    mod.get_zarr_store_uri = get_zarr_store_uri

    logger.info("Storage patched → local filesystem at %s", _OUTPUT_ROOT)
