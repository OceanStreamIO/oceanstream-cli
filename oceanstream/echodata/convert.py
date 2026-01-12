"""Convert raw echosounder files to EchoData Zarr format.

This module provides functions for converting raw EK60/EK80 files to
echopype's EchoData format, saved as Zarr stores for efficient processing.

Functions are designed for both CLI and library use with Prefect flows.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)

# Default chunking for Dask processing
DEFAULT_CHUNKS = {"ping_time": 2000, "range_sample": -1}


def convert_raw_file(
    raw_file: Path,
    output_dir: Path,
    sonar_model: str = "EK80",
    use_dask: bool = True,
    chunks: Optional[dict] = None,
    save_zarr: bool = True,
) -> Path:
    """
    Convert a single raw echosounder file to EchoData Zarr format.
    
    Designed for parallel execution - each file can be processed independently.
    
    Args:
        raw_file: Path to raw .raw file
        output_dir: Directory for output Zarr
        sonar_model: "EK80" or "EK60"
        use_dask: Enable Dask for lazy loading
        chunks: Dask chunk sizes (default: {ping_time: 2000})
        save_zarr: Whether to save to Zarr (False returns EchoData in memory)
    
    Returns:
        Path to output Zarr store
    
    Raises:
        FileNotFoundError: If raw_file doesn't exist
        ValueError: If sonar_model is unsupported
        RuntimeError: If echopype conversion fails
    
    Example (Prefect):
        @task
        def convert_task(raw_file: Path) -> Path:
            return convert_raw_file(raw_file, output_dir=Path("./out"))
    """
    try:
        from echopype.convert.api import open_raw
    except ImportError as e:
        raise ImportError(
            "echopype is required for echodata processing. "
            "Install with: pip install git+https://github.com/OceanStreamIO/echopype-dev.git@oceanstream-iotedge"
        ) from e
    
    if not raw_file.exists():
        raise FileNotFoundError(f"Raw file not found: {raw_file}")
    
    if sonar_model not in ("EK80", "EK60"):
        raise ValueError(f"Unsupported sonar model: {sonar_model}. Use 'EK80' or 'EK60'.")
    
    chunks = chunks or DEFAULT_CHUNKS
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    zarr_path = output_dir / f"{raw_file.stem}.zarr"
    
    logger.info(f"Converting {raw_file.name} with sonar_model={sonar_model}")
    
    try:
        echodata = open_raw(raw_file, sonar_model=sonar_model)
        
        if echodata.beam is None:
            logger.warning(f"No beam data in {raw_file.name}, file may be corrupted or empty")
            return zarr_path
        
        if use_dask and chunks:
            echodata = echodata.chunk(chunks)
        
        if save_zarr:
            logger.info(f"Saving to {zarr_path}")
            echodata.to_zarr(zarr_path, overwrite=True)
        
        return zarr_path
        
    except Exception as e:
        logger.error(f"Error converting {raw_file.name}: {e}")
        raise RuntimeError(f"Failed to convert {raw_file}: {e}") from e


def convert_raw_files(
    raw_files: list[Path],
    output_dir: Path,
    sonar_model: str = "EK80",
    use_dask: bool = True,
    chunks: Optional[dict] = None,
    parallel: bool = True,
    n_workers: int = 4,
) -> list[Path]:
    """
    Convert multiple raw files to EchoData Zarr format.
    
    When parallel=True, uses Dask distributed for parallel conversion.
    When parallel=False, suitable for use within Prefect's own parallelism.
    
    Args:
        raw_files: List of raw file paths
        output_dir: Output directory
        sonar_model: Echosounder model ("EK80" or "EK60")
        use_dask: Enable Dask arrays
        chunks: Dask chunk sizes
        parallel: Enable internal parallelism (set False for Prefect)
        n_workers: Number of Dask workers (if parallel=True)
    
    Returns:
        List of paths to output Zarr stores
    
    Raises:
        ValueError: If raw_files is empty
    """
    if not raw_files:
        return []  # Empty input → empty output    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if parallel and len(raw_files) > 1:
        return _convert_parallel(
            raw_files, output_dir, sonar_model, use_dask, chunks, n_workers        )
    else:
        # Sequential conversion
        return [
            convert_raw_file(
                f, output_dir, sonar_model, use_dask, chunks
            )
            for f in raw_files
        ]


def _convert_parallel(
    raw_files: list[Path],
    output_dir: Path,
    sonar_model: str,
    use_dask: bool,
    chunks: Optional[dict],
    n_workers: int,
) -> list[Path]:
    """Convert files in parallel using Dask distributed."""
    try:
        from dask.distributed import Client, LocalCluster
    except ImportError as e:
        logger.warning(f"Dask distributed not available: {e}. Falling back to sequential.")
        return [
            convert_raw_file(f, output_dir, sonar_model, use_dask, chunks)
            for f in raw_files
        ]
    
    logger.info(f"Starting parallel conversion with {n_workers} workers")
    
    cluster = LocalCluster(n_workers=n_workers, threads_per_worker=1)
    client = Client(cluster)
    
    try:
        futures = []
        for raw_file in raw_files:
            future = client.submit(
                convert_raw_file,
                raw_file,
                output_dir,
                sonar_model,
                use_dask,
                chunks,
            )
            futures.append(future)
        
        results = client.gather(futures)
        return list(results)
    finally:
        client.close()
        cluster.close()


def open_converted(zarr_path: Path) -> "EchoData":
    """
    Open a previously converted EchoData Zarr store.
    
    Args:
        zarr_path: Path to Zarr store created by convert_raw_file
        
    Returns:
        EchoData object
    """
    try:
        from echopype.echodata import EchoData
    except ImportError as e:
        raise ImportError(
            "echopype is required. Install the fork: "
            "pip install git+https://github.com/OceanStreamIO/echopype-dev.git@oceanstream-iotedge"
        ) from e
    
    return EchoData.from_file(zarr_path)


def detect_sonar_model(raw_file: Path) -> str:
    """
    Attempt to auto-detect sonar model from raw file.
    
    Args:
        raw_file: Path to raw echosounder file
        
    Returns:
        "EK80" or "EK60" based on file contents
        
    Note:
        Currently returns "EK80" as default since Saildrone uses EK80.
        Future: implement actual detection based on file header.
    """
    # TODO: Implement actual detection
    # For now, default to EK80 (Saildrone standard)
    logger.debug(f"Auto-detecting sonar model for {raw_file.name}, defaulting to EK80")
    return "EK80"


def _get_output_path(raw_file: Path, output_dir: Path) -> Path:
    """
    Get output Zarr path for a raw file.
    
    Args:
        raw_file: Path to raw .raw file
        output_dir: Output directory
        
    Returns:
        Path for output .zarr store
    """
    return output_dir / f"{raw_file.stem}.zarr"
