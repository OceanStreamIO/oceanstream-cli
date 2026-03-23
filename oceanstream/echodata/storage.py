"""Cloud storage support for echodata Zarr stores.

This module provides functions for reading/writing EchoData and Sv datasets
to cloud storage (Azure Blob, S3, GCS) using fsspec-based Zarr stores.

The module integrates with the oceanstream storage configuration system,
using credentials from either:
1. Environment variables (AZURE_STORAGE_CONNECTION_STRING, etc.)
2. oceanstream configure (stored in ~/.oceanstream/storage.json)

Storage Structure (recommended):
    {container}/echodata/{campaign_id}/
        ├── converted/              # Raw → EchoData conversion
        │   ├── {filename}.zarr/
        │   └── ...
        ├── calibrated/             # Sv (calibrated backscatter)
        │   ├── {filename}_Sv.zarr/
        │   └── ...
        ├── products/               # Derived products (MVBS, NASC)
        │   ├── {filename}_mvbs.zarr/
        │   └── ...
        └── echograms/              # PNG visualizations
            └── ...

Usage:
    from oceanstream.echodata.storage import (
        get_azure_zarr_store,
        save_echodata_to_azure,
        save_sv_to_azure,
    )
    
    # Using environment variables
    store = get_azure_zarr_store("echodata/campaign_1/converted/file.zarr")
    echodata.to_zarr(store, mode="w")
    
    # Using helper function
    save_echodata_to_azure(echodata, campaign_id="campaign_1", filename="file")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import xarray as xr
    from echopype.echodata import EchoData
    import zarr

logger = logging.getLogger(__name__)


def get_azure_credentials(
    connection_string: str | None = None,
) -> tuple[str, str]:
    """Get Azure credentials from parameter, environment or oceanstream config.

    Args:
        connection_string: Explicit connection string (takes priority over env/config).

    Returns:
        Tuple of (connection_string, container_name)

    Raises:
        ValueError: If no credentials found
    """
    container = os.environ.get("AZURE_CONTAINER_NAME", "oceanstream-data")

    # Explicit parameter takes priority
    if connection_string:
        return connection_string, container

    # Try environment variables
    env_conn = (
        os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        or os.environ.get("AZURE_CONNECTION_STRING")
    )
    if env_conn:
        return env_conn, container
    
    # Try oceanstream storage config
    try:
        from oceanstream.storage.manager import load_storage_configuration
        from oceanstream.storage.config import AzureStorageConfig
        
        config = load_storage_configuration()
        azure_config = config.providers.get("azure")
        
        if azure_config and isinstance(azure_config, AzureStorageConfig):
            if azure_config.connection_string:
                return azure_config.connection_string, azure_config.container_name
            elif azure_config.account_name and azure_config.access_key:
                # Build connection string from components
                conn_str = (
                    f"DefaultEndpointsProtocol=https;"
                    f"AccountName={azure_config.account_name};"
                    f"AccountKey={azure_config.access_key};"
                    f"EndpointSuffix=core.windows.net"
                )
                return conn_str, azure_config.container_name
    except (ImportError, FileNotFoundError, ValueError) as e:
        logger.debug(f"Could not load oceanstream storage config: {e}")
    
    raise ValueError(
        "Azure credentials not found. Set AZURE_STORAGE_CONNECTION_STRING environment "
        "variable or run 'oceanstream configure' to set up Azure storage."
    )


def get_azure_filesystem(connection_string: str | None = None):
    """Get an fsspec AzureBlobFileSystem configured with credentials.

    Args:
        connection_string: Explicit connection string (takes priority over env/config).

    Returns:
        adlfs.AzureBlobFileSystem instance

    Raises:
        ValueError: If credentials not found
        ImportError: If adlfs not installed
    """
    try:
        import adlfs
    except ImportError as e:
        raise ImportError(
            "adlfs is required for Azure Blob storage. Install with: pip install adlfs"
        ) from e

    conn_str, _container = get_azure_credentials(connection_string)

    return adlfs.AzureBlobFileSystem(connection_string=conn_str)


def get_azure_zarr_store(
    path: str,
    container: Optional[str] = None,
    mode: str = "w",
    connection_string: str | None = None,
) -> "zarr.storage.FSStore":
    """Get a Zarr store backed by Azure Blob Storage.

    Args:
        path: Path within the container (e.g., "echodata/campaign/file.zarr")
        container: Container name (default: from credentials)
        mode: Access mode - "r" (read), "w" (write), "a" (append)
        connection_string: Explicit connection string.

    Returns:
        zarr.storage.FSStore configured for Azure

    Example:
        store = get_azure_zarr_store("echodata/test/data.zarr")
        ds.to_zarr(store, mode="w")
    """
    import zarr

    _conn_str, default_container = get_azure_credentials(connection_string)
    container = container or default_container

    fs = get_azure_filesystem(connection_string)
    full_path = f"{container}/{path}"

    return zarr.storage.FSStore(full_path, fs=fs, mode=mode)


def get_zarr_store_uri(
    path: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> str:
    """Get the Azure Blob URI for a zarr store path.

    Args:
        path: Path within container
        container: Container name (default: from credentials)
        connection_string: Explicit connection string.

    Returns:
        URI like "abfs://container/path"
    """
    _, default_container = get_azure_credentials(connection_string)
    container = container or default_container
    return f"abfs://{container}/{path}"


def build_echodata_path(
    campaign_id: str,
    filename: str,
    stage: str = "converted",
) -> str:
    """Build the standard path for echodata storage.
    
    Args:
        campaign_id: Campaign identifier
        filename: Base filename (without extension)
        stage: Processing stage - "converted", "calibrated", "products"
        
    Returns:
        Path like "echodata/{campaign_id}/{stage}/{filename}.zarr"
    """
    # Sanitize filename
    filename = Path(filename).stem
    return f"echodata/{campaign_id}/{stage}/{filename}.zarr"


def save_echodata_to_azure(
    echodata: "EchoData",
    campaign_id: str,
    filename: Optional[str] = None,
    container: Optional[str] = None,
    overwrite: bool = True,
    connection_string: str | None = None,
) -> str:
    """Save EchoData to Azure Blob Storage.
    
    Args:
        echodata: EchoData object from echopype
        campaign_id: Campaign identifier for organizing data
        filename: Output filename (default: from echodata source file)
        container: Azure container (default: from credentials)
        overwrite: Whether to overwrite existing data
        
    Returns:
        Azure URI of saved zarr store
        
    Example:
        uri = save_echodata_to_azure(echodata, campaign_id="TPOS_2023")
        print(f"Saved to {uri}")
    """
    # Get filename from echodata if not provided
    if filename is None:
        source_file = echodata.source_file
        if source_file:
            filename = Path(source_file).stem
        else:
            filename = "echodata"
    
    path = build_echodata_path(campaign_id, filename, stage="converted")
    store = get_azure_zarr_store(path, container=container, connection_string=connection_string)

    logger.info(f"Saving EchoData to Azure: {path}")
    echodata.to_zarr(store, overwrite=overwrite)

    return get_zarr_store_uri(path, container, connection_string=connection_string)


def save_sv_to_azure(
    sv_dataset: "xr.Dataset",
    campaign_id: str,
    filename: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> str:
    """Save calibrated Sv dataset to Azure Blob Storage.
    
    Args:
        sv_dataset: xarray Dataset with Sv data
        campaign_id: Campaign identifier
        filename: Base filename
        container: Azure container (default: from credentials)
        
    Returns:
        Azure URI of saved zarr store
    """
    path = build_echodata_path(campaign_id, f"{filename}_Sv", stage="calibrated")
    store = get_azure_zarr_store(
        path, container=container, connection_string=connection_string,
    )

    logger.info(f"Saving Sv dataset to Azure: {path}")
    import xarray as xr_mod
    if isinstance(sv_dataset, xr_mod.Dataset):
        from oceanstream.echodata.utils.encoding import fix_chunking
        sv_dataset = fix_chunking(sv_dataset)
    sv_dataset.to_zarr(store, mode="w")

    return get_zarr_store_uri(path, container, connection_string=connection_string)


def save_product_to_azure(
    dataset: "xr.Dataset",
    campaign_id: str,
    filename: str,
    product_type: str,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> str:
    """Save derived product (MVBS, NASC, etc.) to Azure.
    
    Args:
        dataset: xarray Dataset with product data
        campaign_id: Campaign identifier
        filename: Base filename
        product_type: Product type ("mvbs", "nasc", etc.)
        container: Azure container
        
    Returns:
        Azure URI of saved zarr store
    """
    path = build_echodata_path(campaign_id, f"{filename}_{product_type}", stage="products")
    store = get_azure_zarr_store(
        path, container=container, connection_string=connection_string,
    )

    logger.info(f"Saving {product_type.upper()} to Azure: {path}")
    import xarray as xr_mod
    if isinstance(dataset, xr_mod.Dataset):
        from oceanstream.echodata.utils.encoding import fix_chunking
        dataset = fix_chunking(dataset)
    dataset.to_zarr(store, mode="w")

    return get_zarr_store_uri(path, container, connection_string=connection_string)


def open_echodata_from_azure(
    campaign_id: str,
    filename: str,
    stage: str = "converted",
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> "EchoData":
    """Open EchoData from Azure Blob Storage.
    
    Args:
        campaign_id: Campaign identifier
        filename: Base filename
        stage: Processing stage
        container: Azure container
        
    Returns:
        EchoData object
    """
    try:
        import echopype as ep
    except ImportError as e:
        raise ImportError("echopype required to open EchoData") from e
    
    path = build_echodata_path(campaign_id, filename, stage=stage)
    uri = get_zarr_store_uri(path, container, connection_string=connection_string)

    logger.info(f"Opening EchoData from Azure: {uri}")
    return ep.open_converted(uri)


def open_sv_from_azure(
    campaign_id: str,
    filename: str,
    container: Optional[str] = None,
    chunks: Optional[dict] = None,
    connection_string: str | None = None,
) -> "xr.Dataset":
    """Open Sv dataset from Azure Blob Storage.
    
    Args:
        campaign_id: Campaign identifier
        filename: Base filename
        container: Azure container
        chunks: Dask chunking for lazy loading
        
    Returns:
        xarray Dataset with Sv data
    """
    import xarray as xr
    
    path = build_echodata_path(campaign_id, f"{filename}_Sv", stage="calibrated")

    conn_str, default_container = get_azure_credentials(connection_string)
    container = container or default_container
    full_path = f"abfs://{container}/{path}"

    logger.info(f"Opening Sv from Azure: {full_path}")

    storage_options = {"connection_string": conn_str}
    
    if chunks:
        return xr.open_zarr(full_path, chunks=chunks, storage_options=storage_options)
    return xr.open_zarr(full_path, storage_options=storage_options)


def list_campaign_data(
    campaign_id: str,
    stage: Optional[str] = None,
    container: Optional[str] = None,
    connection_string: str | None = None,
) -> list[str]:
    """List all zarr stores for a campaign.
    
    Args:
        campaign_id: Campaign identifier
        stage: Filter by stage (converted, calibrated, products)
        container: Azure container
        
    Returns:
        List of zarr store paths
    """
    fs = get_azure_filesystem(connection_string)
    _, default_container = get_azure_credentials(connection_string)
    container = container or default_container
    
    base_path = f"{container}/echodata/{campaign_id}"
    if stage:
        base_path = f"{base_path}/{stage}"
    
    def find_zarr_stores(path: str) -> list[str]:
        """Recursively find .zarr stores."""
        stores = []
        try:
            items = fs.ls(path, detail=False)
            for item in items:
                if item.endswith(".zarr"):
                    stores.append(item)
                elif fs.isdir(item):
                    # Recurse into subdirectories
                    stores.extend(find_zarr_stores(item))
        except FileNotFoundError:
            pass
        return stores
    
    return find_zarr_stores(base_path)


# Convenience function to check Azure availability
def is_azure_configured() -> bool:
    """Check if Azure storage is configured and accessible.
    
    Returns:
        True if Azure credentials are available
    """
    try:
        get_azure_credentials()
        return True
    except ValueError:
        return False


def ensure_container_exists(
    container_name: str,
    connection_string: str | None = None,
    public_access: str | None = None,
) -> None:
    """Create the Azure Blob container if it does not already exist."""
    from azure.storage.blob import BlobServiceClient

    conn_str = (
        connection_string
        or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        or os.environ.get("AZURE_CONNECTION_STRING")
    )
    if not conn_str:
        conn_str, _ = get_azure_credentials()

    client = BlobServiceClient.from_connection_string(conn_str)
    container_client = client.get_container_client(container_name)
    if not container_client.exists():
        logger.info("Creating container '%s'", container_name)
        container_client.create_container(public_access=public_access)
    else:
        logger.debug("Container '%s' already exists", container_name)


def generate_container_name(cruise_id: str) -> str:
    """Generate a unique Azure-safe container name from a cruise id."""
    import re
    import uuid
    from datetime import datetime as _dt

    date_str = _dt.now().strftime("%Y%m%d")
    unique_id = uuid.uuid4().hex[:8]
    raw_name = f"{cruise_id}{date_str}{unique_id}".lower()
    sanitized = re.sub(r"[^a-z0-9-]", "", raw_name)
    return sanitized[:63]


def save_dataset_to_netcdf(
    ds: "xr.Dataset",
    *,
    container_name: str,
    ds_path: str = "data.nc",
    base_local_temp_path: str = "/tmp/oceanstream/netcdfdata",
    is_temp_dir: bool = True,
    compression_level: int = 5,
    connection_string: str | None = None,
) -> tuple[Path, int]:
    """Write dataset to NetCDF locally and upload to Azure Blob.

    Returns ``(local_path, netcdf_size_bytes)``.
    """
    import gc as _gc

    local_path = Path(base_local_temp_path).expanduser() / container_name / ds_path
    local_path.parent.mkdir(parents=True, exist_ok=True)

    encoding = _netcdf_encoding(ds, compression_level)
    ds.to_netcdf(local_path, engine="netcdf4", format="NETCDF4", encoding=encoding, compute=True)
    nc_size = local_path.stat().st_size

    upload_file_to_blob(str(local_path), ds_path, container_name, connection_string=connection_string)

    try:
        ds.close()
    except AttributeError:
        pass
    del ds
    _gc.collect()

    logger.info("Saved and uploaded NetCDF: %s (%d bytes)", local_path, nc_size)
    return local_path, nc_size


def zip_and_save_netcdf_files(
    file_paths: list[str | Path],
    zip_name: str,
    container_name: str,
    *,
    tmp_dir: str | None = None,
    connection_string: str | None = None,
) -> None:
    """Zip a list of NetCDF files and upload the archive to Azure Blob."""
    import shutil
    import tempfile
    import zipfile

    if tmp_dir is not None:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir, exist_ok=True)
    else:
        tmp_dir = tempfile.mkdtemp()

    zip_path = Path(tmp_dir) / "archive.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in file_paths:
            archive.write(p, arcname=Path(p).name)

    upload_file_to_blob(str(zip_path), zip_name, container_name, connection_string=connection_string)
    logger.info("Uploaded archive %s to %s", zip_name, container_name)


def list_zarr_files(
    container_name: str,
    *,
    cruise_id: str | None = None,
    file_names: list[str] | None = None,
    connection_string: str | None = None,
) -> list[Path]:
    """List zarr stores in a container, optionally filtered by cruise / filenames."""
    fs = get_azure_filesystem(connection_string)

    path = container_name
    if cruise_id is not None:
        path = f"{container_name}/{cruise_id}"

    if file_names is not None and cruise_id is not None:
        return [Path(f"{path}/{fn}.zarr") for fn in file_names]

    results: list[Path] = []

    def _walk(p: str) -> None:
        for blob in fs.ls(p, detail=True):
            if blob["type"] == "directory" and not blob["name"].endswith(".zarr"):
                _walk(blob["name"])
            elif blob["name"].endswith(".zarr"):
                results.append(Path(blob["name"]))

    _walk(path)
    return results


def upload_file_to_blob(
    local_path: str,
    blob_path: str,
    container_name: str,
    connection_string: str | None = None,
) -> None:
    """Upload a local file to Azure Blob Storage."""
    from azure.storage.blob import BlobServiceClient

    conn_str = (
        connection_string
        or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        or os.environ.get("AZURE_CONNECTION_STRING")
    )
    if not conn_str:
        conn_str, _ = get_azure_credentials()

    client = BlobServiceClient.from_connection_string(conn_str)
    container_client = client.get_container_client(container_name)
    with open(local_path, "rb") as data:
        container_client.upload_blob(name=blob_path, data=data, overwrite=True)


# ── Internal helpers ────────────────────────────────────────────────


def _netcdf_encoding(ds: "xr.Dataset", compression_level: int = 5) -> dict:
    """Build per-variable NetCDF4 encoding with zlib compression."""
    encoding = {}
    for var in ds.data_vars:
        enc: dict = {"zlib": True, "complevel": compression_level}
        dtype = ds[var].dtype
        if dtype.kind == "f":
            enc["dtype"] = "float32"
        encoding[var] = enc
    return encoding
