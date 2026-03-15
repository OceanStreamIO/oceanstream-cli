"""NetCDF export utilities for echodata products.

Provides functions for saving xarray Datasets to NetCDF4 format
(locally or to Azure Blob Storage) and creating ZIP bundles.
"""

from __future__ import annotations

import logging
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)


def save_to_netcdf(
    ds: "xr.Dataset",
    output_path: Path | str,
    compression_level: int = 5,
    write_chunks: Optional[dict] = None,
) -> Path:
    """Save an xarray Dataset to a local NetCDF4 file with zlib compression.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset to write.
    output_path : Path or str
        Destination file path (should end in ``.nc``).
    compression_level : int
        zlib compression level (1–9, default 5).
    write_chunks : dict, optional
        If provided the dataset is re-chunked before writing.

    Returns
    -------
    Path
        The written file path.
    """
    from oceanstream.echodata.utils.encoding import get_variable_encoding

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if write_chunks:
        ds = ds.chunk(write_chunks)

    encoding = get_variable_encoding(ds, compression_level)

    ds.to_netcdf(
        output_path,
        engine="netcdf4",
        format="NETCDF4",
        encoding=encoding,
        compute=True,
    )

    logger.info(
        "Saved NetCDF: %s (%.1f MB)",
        output_path,
        output_path.stat().st_size / 1e6,
    )
    return output_path


def save_to_netcdf_azure(
    ds: "xr.Dataset",
    ds_path: str,
    container_name: str,
    compression_level: int = 5,
    write_chunks: Optional[dict] = None,
    base_temp_path: Optional[str] = None,
    max_retries: int = 3,
    backoff_sec: int = 5,
) -> tuple[Path, int]:
    """Save a Dataset to NetCDF locally, then upload to Azure Blob Storage.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset to write.
    ds_path : str
        Blob path inside the container (e.g. ``"campaign/product.nc"``).
    container_name : str
        Azure Blob container name.
    compression_level : int
        zlib compression level.
    write_chunks : dict, optional
        If provided the dataset is re-chunked before writing.
    base_temp_path : str, optional
        Local temp directory root (default: system temp).
    max_retries : int
        Number of upload retry attempts.
    backoff_sec : int
        Initial back-off between retries (doubled each attempt).

    Returns
    -------
    tuple[Path, int]
        ``(local_path, file_size_bytes)``
    """
    from oceanstream.echodata.storage import get_azure_filesystem

    if base_temp_path is None:
        base_temp_path = tempfile.gettempdir()

    local_path = Path(base_temp_path) / container_name / ds_path
    local_path.parent.mkdir(parents=True, exist_ok=True)

    save_to_netcdf(ds, local_path, compression_level=compression_level, write_chunks=write_chunks)

    file_size = local_path.stat().st_size
    blob_path = f"{container_name}/{ds_path}"

    fs = get_azure_filesystem()
    for attempt in range(1, max_retries + 1):
        try:
            fs.put(str(local_path), blob_path, overwrite=True)
            logger.info("Uploaded %s to Azure (%d bytes)", blob_path, file_size)
            break
        except Exception as exc:
            if attempt == max_retries:
                raise
            sleep = backoff_sec * 2 ** (attempt - 1)
            logger.warning(
                "Upload failed (attempt %d/%d): %s — retrying in %ds",
                attempt,
                max_retries,
                exc,
                sleep,
            )
            time.sleep(sleep)

    return local_path, file_size


def zip_netcdf_files(
    file_paths: list[Path | str],
    zip_path: Path | str,
) -> Path:
    """Create a ZIP archive containing the listed NetCDF files.

    Parameters
    ----------
    file_paths : list of Path
        NetCDF files to bundle.
    zip_path : Path or str
        Output ZIP file path.

    Returns
    -------
    Path
        The created ZIP file path.
    """
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for fp in file_paths:
            fp = Path(fp)
            archive.write(fp, arcname=fp.name)
            logger.info("Added to archive: %s", fp.name)

    logger.info(
        "Created ZIP: %s (%.1f MB, %d files)",
        zip_path,
        zip_path.stat().st_size / 1e6,
        len(file_paths),
    )
    return zip_path
