"""Read RDI (Teledyne) ADCP binary files via dolfyn."""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING

# Apply compat patches before dolfyn imports numpy/scipy internals
import oceanstream.adcp._compat  # noqa: F401

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=UserWarning, message="pkg_resources")
    warnings.filterwarnings("ignore", category=FutureWarning)
    from dolfyn.io.rdi import read_rdi as _dolfyn_read_rdi

if TYPE_CHECKING:
    import xarray as xr


def read_rdi(path: Path | str) -> xr.Dataset:
    """Read an RDI (Teledyne) ADCP binary file.

    Parameters
    ----------
    path : Path or str
        Path to the ``.raw`` binary file.

    Returns
    -------
    xr.Dataset
        Raw ADCP data with velocity in beam coordinates, amplitude,
        correlation, heading/pitch/roll, pressure, temperature, etc.
        Key attributes: ``inst_make``, ``inst_model``, ``freq``,
        ``n_beams``, ``coord_sys``, ``n_cells``, ``cell_size``.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"RDI file not found: {path}")
    if not path.suffix == ".raw":
        raise ValueError(f"Expected .raw file, got: {path.suffix}")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        ds = _dolfyn_read_rdi(str(path))

    return ds


def scan_rdi_files(directory: Path | str) -> list[Path]:
    """Find all RDI ``.raw`` ADCP files in a directory.

    Parameters
    ----------
    directory : Path or str
        Directory to scan.

    Returns
    -------
    list[Path]
        Sorted list of ``.raw`` file paths.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    return sorted(directory.glob("*.raw"))
