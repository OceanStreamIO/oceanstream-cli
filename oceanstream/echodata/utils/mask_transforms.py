"""Mask and array transformation utilities for denoising and analysis.

General-purpose helpers for resampling, dB/linear conversion,
shape manipulation, and dask-safe statistical operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

import numpy as np
import xarray as xr

if TYPE_CHECKING:
    import dask.array as da


def downsample(
    dataset: xr.DataArray,
    coordinates: dict[str, int],
    operation: str = "mean",
    is_log: bool = False,
) -> xr.DataArray:
    """Downsample a DataArray by coarsening on specified dimensions.

    Parameters
    ----------
    dataset : xr.DataArray
        Data to resample.
    coordinates : dict
        Mapping of dimension name → coarsen window size.
    operation : str
        ``"mean"`` or ``"sum"``.
    is_log : bool
        If True, convert to linear before aggregation and back after.
    """
    if operation not in ("mean", "sum"):
        raise ValueError(f"Unsupported operation '{operation}'. Use 'mean' or 'sum'.")
    for k in coordinates:
        if k not in dataset.dims:
            raise ValueError(f"Coordinate '{k}' not in dataset dimensions {list(dataset.dims)}")

    if is_log:
        dataset = lin(dataset)

    coarsened = dataset.coarsen(coordinates, boundary="pad")
    dataset = coarsened.mean() if operation == "mean" else coarsened.sum()

    if is_log:
        dataset = log(dataset)

    return dataset


def upsample(dataset: xr.DataArray, target: xr.DataArray) -> xr.DataArray:
    """Upsample *dataset* to match *target* dimensions via nearest interpolation."""
    return dataset.interp_like(target, method="nearest")


def log(linear: xr.DataArray, parallelized: bool = True) -> xr.DataArray:
    """Convert linear scale to decibels (10 log10).

    Values ≤ 0 are mapped to ``-999`` (fisheries acoustics convention
    for empty water / vacant sample).
    """
    back_list = False
    back_single = False
    if not isinstance(linear, xr.DataArray):
        if isinstance(linear, list):
            linear = xr.DataArray(linear)
            back_list = True
        else:
            linear = xr.DataArray([linear])
            back_single = True

    if parallelized:
        db = xr.apply_ufunc(
            lambda x: 10 * np.log10(x),
            linear,
            dask="parallelized",
            vectorize=True,
            output_dtypes=[np.float64],
        )
    else:
        db = xr.apply_ufunc(lambda x: 10 * np.log10(x), linear)

    db = xr.where(db.isnull(), -999, db)
    db = xr.where(linear == 0, -999, db)

    if back_list:
        return db.values
    if back_single:
        return db.values[0]
    return db


def lin(db: xr.DataArray) -> xr.DataArray:
    """Convert decibels to linear scale, preserving NaN."""
    return xr.where(db.isnull(), np.nan, 10 ** (db / 10))


def line_to_square(
    one: xr.DataArray,
    two: xr.DataArray,
    dim: str,
) -> np.ndarray:
    """Broadcast a 1-D array to match a 2-D array along *dim*.

    Parameters
    ----------
    one : xr.DataArray
        1-D source data.
    two : xr.DataArray
        2-D shape reference.
    dim : str
        Dimension in *two* along which to repeat *one*.

    Returns
    -------
    np.ndarray or dask.array.Array
    """
    import dask.array as da

    length = len(two[dim])

    if isinstance(one.data, da.Array):
        return da.repeat(one.data[..., np.newaxis], length, axis=-1)

    return np.repeat(one.values[..., np.newaxis], length, axis=-1)


def block_nanmedian(
    block: Union[np.ndarray, da.Array],
    i: int,
    n: int,
    axis: int,
) -> float:
    """Nanmedian of a window ``[i-n, i+n]`` along *axis*."""
    import dask.array as da

    start = max(0, i - n)
    end = min(block.shape[axis], i + n + 1)

    if isinstance(block, da.Array):
        indices = da.arange(start, end, dtype=int)
        return float(da.nanmedian(da.take(block, indices, axis)).compute())

    indices = np.arange(start, end)
    return float(np.nanmedian(np.take(block, indices, axis=axis)))


def rolling_median_block(
    block: Union[np.ndarray, da.Array],
    window_half_size: int,
    axis: int,
) -> np.ndarray:
    """Apply a rolling nanmedian along *axis* with the given half-window."""
    import dask.array as da

    shape = block.shape[axis]
    if isinstance(block, da.Array):
        chunk_size = block.chunks[axis][0]
        results = []
        for i in range(0, shape, chunk_size):
            end = min(i + chunk_size, shape)
            results.extend(
                block_nanmedian(block, j, window_half_size, axis)
                for j in range(i, end)
            )
        return np.array(results)

    return np.array(
        [block_nanmedian(block, i, window_half_size, axis) for i in range(shape)]
    )


def dask_nanmedian(
    array: Union[xr.DataArray, np.ndarray, da.Array],
    axis: int | None = None,
) -> da.Array:
    """Compute nanmedian, converting to dask array if needed."""
    import dask.array as da

    data = array.data if isinstance(array, xr.DataArray) else array
    if isinstance(data, np.ndarray):
        data = da.from_array(data)
    elif not isinstance(data, da.Array):
        raise TypeError(f"Cannot convert {type(data)} to dask array")
    return da.nanmedian(data, axis=axis)


def dask_nanmean(
    array: Union[xr.DataArray, np.ndarray, da.Array],
    axis: int | None = None,
) -> da.Array:
    """Compute nanmean, converting to dask array if needed."""
    import dask.array as da

    data = array.data if isinstance(array, xr.DataArray) else array
    if isinstance(data, np.ndarray):
        data = da.from_array(data)
    elif not isinstance(data, da.Array):
        raise TypeError(f"Cannot convert {type(data)} to dask array")
    return da.nanmean(data, axis=axis)
