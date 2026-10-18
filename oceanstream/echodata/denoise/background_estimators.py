"""Background noise estimators the batch pipeline can choose between.

``echopype`` (the default) is echopype's estimator as :mod:`oceanstream.echodata.products`
reproduces it: for each block of ``ping_num`` pings, the mean power over blocks of
``range_sample_num`` samples, with spreading and absorption removed, and the noise level is
the *minimum* of those block means along range.

``quantile`` is experimental, written for R/V Roger Revelle cruise RR2205, whose EK80
channels each ping about every 39 s and whose deep data show single pings with a raised
noise floor. Following those pings needs small ping blocks, but the minimum of many noisy
block means sits below the true noise level, and further below the fewer samples each block
holds, so small blocks remove too little. A low quantile of the block means (the preset's
``depth_quantile``, default 0.15) is far less biased by the noisiest blocks. Everything
else, inputs and output included, matches the echopype estimator, so the correction step
(:func:`oceanstream.echodata.products.background_corrected`) is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from oceanstream.echodata.products import _log2lin, _range_terms
from oceanstream.echodata.products import estimate_background_level as echopype_level

if TYPE_CHECKING:
    import xarray as xr

ESTIMATORS = ("echopype", "quantile")
DEFAULT_QUANTILE = 0.15


def estimate_background_level_quantile(
    ds: xr.Dataset,
    ping_num: int,
    range_sample_num: int,
    background_noise_max: float | None = None,
    quantile: float = DEFAULT_QUANTILE,
) -> xr.DataArray:
    """Noise level per ping from a low quantile of block means along range (experimental).

    Same contract as :func:`oceanstream.echodata.products.estimate_background_level`.
    """
    spreading_loss, absorption_loss = _range_terms(ds)
    power_cal = _log2lin(ds["Sv"] - spreading_loss - absorption_loss)
    binned = 10 * np.log10(
        power_cal.coarsen(ping_time=ping_num, range_sample=range_sample_num, boundary="pad").mean()
    )
    noise = binned.quantile(quantile, dim="range_sample", skipna=True).drop_vars("quantile")
    # Align each bin to the first ping it covers, then carry it forward.
    noise = noise.assign_coords(ping_time=ping_num * np.arange(len(noise["ping_time"])))
    if background_noise_max is not None:
        noise = noise.where(noise < background_noise_max, background_noise_max)
    ping_index = np.arange(ds.sizes["ping_time"])
    return noise.reindex({"ping_time": ping_index}, method="ffill").assign_coords(
        ping_time=ds["ping_time"]
    )


def estimate_background_level(  # noqa: PLR0913 - keyword-only tuning knobs
    ds: xr.Dataset,
    estimator: str,
    *,
    ping_num: int,
    range_sample_num: int,
    background_noise_max: float | None = None,
    quantile: float | None = None,
) -> xr.DataArray:
    """Noise level per ping with the named estimator."""
    if estimator == "echopype":
        return echopype_level(ds, ping_num, range_sample_num, background_noise_max)
    if estimator == "quantile":
        return estimate_background_level_quantile(
            ds, ping_num, range_sample_num, background_noise_max,
            DEFAULT_QUANTILE if quantile is None else float(quantile),
        )
    raise ValueError(f"Unknown background estimator {estimator!r}; choose one of {ESTIMATORS}.")
