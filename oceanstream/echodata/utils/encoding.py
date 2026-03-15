"""Zarr and NetCDF encoding utilities.

Helpers for harmonising Dask chunk hints with Zarr encoding and
generating compression-aware encoding dictionaries for NetCDF4.
"""

from __future__ import annotations

import xarray as xr


def fix_chunking(ds: xr.Dataset, *, tiny_limit: int = 10_000) -> xr.Dataset:
    """Harmonise Zarr-chunk hints with current Dask chunking.

    - Variables with fewer than *tiny_limit* elements are computed to NumPy
      (one scalar / small vector — no memory penalty).
    - For larger variables an incompatible ``encoding["chunks"]`` is dropped
      so that ``to_zarr`` can infer correct chunks from the Dask graph.

    Parameters
    ----------
    ds : xr.Dataset
        Input dataset (may be lazy / Dask-backed).
    tiny_limit : int
        Element count threshold for eager materialisation.

    Returns
    -------
    xr.Dataset
        Copy with cleaned encoding hints.
    """
    ds = ds.copy()

    for name, var in list(ds.variables.items()):
        # Case A: tiny array → compute to NumPy
        if var.size <= tiny_limit:
            ds[name] = (var.dims, var.compute().data)
            ds[name].encoding.clear()
            continue

        # Case B: larger array → fix mismatched hint
        if "chunks" in var.encoding:
            dask_chunks = getattr(var.data, "chunks", None)

            if dask_chunks is None or var.encoding["chunks"] != tuple(
                c[0] for c in dask_chunks
            ):
                var.encoding.pop("chunks", None)

    return ds


def get_variable_encoding(
    ds: xr.Dataset,
    compression_level: int = 5,
) -> dict[str, dict]:
    """Generate a NetCDF4 encoding dict with zlib compression for numeric variables.

    String and object variables are left uncompressed (unsupported by zlib).

    Parameters
    ----------
    ds : xr.Dataset
        Dataset whose ``data_vars`` will be encoded.
    compression_level : int
        zlib compression level (1–9).

    Returns
    -------
    dict
        Mapping ``{var_name: encoding_dict}`` suitable for
        ``ds.to_netcdf(encoding=...)``.
    """
    encoding: dict[str, dict] = {}
    for var in ds.data_vars:
        if ds[var].dtype.kind in {"U", "S", "O"}:
            encoding[var] = {}
        else:
            encoding[var] = {
                "zlib": True,
                "complevel": compression_level,
            }
    return encoding
