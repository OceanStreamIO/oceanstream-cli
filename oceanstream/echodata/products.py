"""Denoised and pruned Sv stored as masks over a shared Sv source.

A denoised store used to be a full copy of Sv with some cells set to NaN, and
a pruned store a second copy with some pings dropped. Every step that produces
them either removes data or, for background removal, subtracts a noise level
that is one number per channel per ping. So they are stored as that
information instead, and rebuilt on open:

``{day}--{cat}--denoised.zarr`` — *masked Sv*
    ``denoise_flags``    uint8 (channel, ping_time, range_sample), one bit per step
    ``background_noise`` float64 (channel, ping_time), noise level before the
                         range-dependent terms; NaN where background was not run
    attrs pointing at the source Sv store and fingerprinting it

``{day}--{cat}--pruned.zarr`` — *pruned view*
    ``keep_ping``        bool (ping_time) over the parent's pings

Sources and parents are recorded as ``(container, path)`` relative to the
storage backend, or — by writers that address stores by URL — with an empty
container and an absolute URI or path.

Readers never see this: every backend's ``open_sv_from_azure`` passes what it
opened through :func:`resolve_product`, which returns an ordinary Sv dataset.
Stores written the old way carry no product marker and pass straight through.

The background arithmetic is shared by the writer and the reader
(:func:`background_corrected`), so a rebuilt Sv is bit-identical to what the
denoiser produced.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

PRODUCT_ATTR = "oceanstream_product"
MASKED_SV = "masked_sv"
PRUNED_VIEW = "pruned_view"
FORMAT_VERSION = 1

#: One bit per step that can remove a cell. ``other`` catches any cell the
#: combined mask removed that no stage cube accounts for, so a rebuild is exact
#: even if the two ever disagree. ``legacy`` marks cells recovered from an old
#: full store, where the per-step breakdown no longer exists.
FLAG_BITS: dict[str, int] = {
    "impulse": 1,
    "attenuation": 2,
    "transient": 4,
    "background": 8,
    "clip": 16,
    "seabed": 32,
    "other": 64,
    "legacy": 128,
}

#: Bits that made up the pre-background ``noise_mask`` variable.
MASK_STAGE_BITS = (
    FLAG_BITS["impulse"] | FLAG_BITS["attenuation"] | FLAG_BITS["transient"] | FLAG_BITS["other"]
)

#: Attribute on a resolved dataset's ``denoise_flags`` holding the store's own
#: attributes (source, fingerprint, thresholds), for :func:`extend_masked`.
PRODUCT_ATTRS_KEY = "oceanstream_product_attrs"

#: ``open(zarr_path, container) -> Dataset``, already resolving products.
Opener = Callable[[str, "str | None"], "xr.Dataset"]


class StaleProductError(RuntimeError):
    """The Sv source changed after the masks were computed against it."""


# ── background noise (De Robertis & Higginbottom 2007, as echopype does it) ──


def _log2lin(data):
    return 10 ** (data / 10)


def _lin2log(data):
    return 10 * np.log10(data)


def _range_terms(ds: xr.Dataset) -> tuple[xr.DataArray, xr.DataArray]:
    """Spreading and absorption loss, exactly as ``echopype.clean`` computes them."""
    spreading_loss = 20 * np.log10(ds["echo_range"].where(ds["echo_range"] >= 1, other=1))
    absorption_loss = 2 * ds["sound_absorption"] * ds["echo_range"]
    return spreading_loss, absorption_loss


def estimate_background_level(
    ds: xr.Dataset,
    ping_num: int,
    range_sample_num: int,
    background_noise_max: float | None = None,
) -> xr.DataArray:
    """The noise level per ping, before the range-dependent terms are added.

    This is the first half of ``echopype.clean.estimate_background_noise``;
    that function adds spreading and absorption loss to it and returns the
    full-resolution ``Sv_noise``. Keeping the per-ping level is what lets the
    correction be stored in a few hundred kilobytes instead of a copy of Sv.
    """
    spreading_loss, absorption_loss = _range_terms(ds)
    power_cal = _log2lin(ds["Sv"] - spreading_loss - absorption_loss)
    power_cal_binned_avg = 10 * np.log10(
        power_cal.coarsen(
            ping_time=ping_num,
            range_sample=range_sample_num,
            boundary="pad",
        ).mean()
    )
    noise = power_cal_binned_avg.min(dim="range_sample", skipna=True)

    # Align each bin to the first ping it covers, then carry it forward.
    noise = noise.assign_coords(ping_time=ping_num * np.arange(len(noise["ping_time"])))
    if background_noise_max is not None:
        noise = noise.where(noise < background_noise_max, background_noise_max)
    ping_index = np.arange(ds.sizes["ping_time"])
    return noise.reindex({"ping_time": ping_index}, method="ffill").assign_coords(
        ping_time=ds["ping_time"]
    )


def background_corrected(
    sv: xr.DataArray,
    level: xr.DataArray,
    ds: xr.Dataset,
    snr_threshold: float | None,
) -> xr.DataArray:
    """``Sv`` with the background noise subtracted and low-SNR cells set to NaN.

    Mirrors ``echopype.clean.remove_background_noise`` operation for operation,
    including the order of the additions, so the floats come out identical.
    """
    spreading_loss, absorption_loss = _range_terms(ds)
    sv_noise = level + spreading_loss + absorption_loss
    linear = _log2lin(sv) - _log2lin(sv_noise)
    corrected = _lin2log(linear.where(linear > 0, other=np.nan))
    if snr_threshold is not None:
        corrected = corrected.where(corrected - sv_noise > snr_threshold, other=np.nan)
    return corrected


def parse_db(value: Any) -> float | None:
    """``"3.0dB"`` → 3.0; numbers pass through; ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        return float(value.replace("dB", "").strip())
    return float(value)


# ── writing ────────────────────────────────────────────────────────────────


def fingerprint(ds: xr.Dataset) -> dict[str, Any]:
    """What must still hold of the source for the masks to apply to it."""
    times = ds["ping_time"].values
    return {
        "sv_shape": [int(n) for n in ds["Sv"].shape],
        "sv_dims": [str(d) for d in ds["Sv"].dims],
        "ping_count": int(ds.sizes["ping_time"]),
        "first_ping": str(times[0]) if len(times) else "",
        "last_ping": str(times[-1]) if len(times) else "",
        "processing_time": str(ds.attrs.get("processing_time", "")),
    }


def build_flags(
    reference: xr.DataArray,
    steps: Mapping[str, xr.DataArray],
) -> xr.DataArray:
    """Pack boolean step masks into one uint8 cube shaped like *reference*.

    A cell only ever carries the bit of a step when that step removed it, so
    callers pass masks already restricted to cells that were still finite.
    """
    flags = np.zeros(reference.shape, dtype=np.uint8)
    for name, mask in steps.items():
        if mask is None:
            continue
        bit = FLAG_BITS[name]
        values = np.asarray(mask.broadcast_like(reference).transpose(*reference.dims).values)
        flags[values.astype(bool)] |= np.uint8(bit)

    import xarray as xr

    return xr.DataArray(
        flags,
        dims=reference.dims,
        coords={d: reference[d] for d in reference.dims if d in reference.coords},
        name="denoise_flags",
        attrs={
            "long_name": "Cells removed by denoising, one bit per step",
            "flag_masks": list(FLAG_BITS.values()),
            "flag_meanings": " ".join(FLAG_BITS),
        },
    )


def masked_sv_dataset(
    *,
    source: xr.Dataset,
    flags: xr.DataArray,
    background_level: xr.DataArray | None,
    background_snr: Mapping[str, float | None] | None,
    source_container: str | None,
    source_path: str,
    store_container: str | None,
    sv_attrs: Mapping[str, Any],
    dataset_attrs: Mapping[str, Any],
    chunks: Mapping[str, int] | None = None,
) -> xr.Dataset:
    """Assemble the masked-Sv store for *source*. Nothing is written here."""
    import xarray as xr

    channels = source["channel"].values
    if background_level is None:
        level = xr.DataArray(
            np.full((len(channels), source.sizes["ping_time"]), np.nan),
            dims=("channel", "ping_time"),
        )
    else:
        level = background_level.transpose("channel", "ping_time")
    level = level.assign_coords(channel=source["channel"], ping_time=source["ping_time"])
    level = level.astype(np.float64).rename("background_noise")
    level.attrs = {
        "long_name": "Background noise level per ping, before spreading and absorption loss",
        "units": "dB re 1 m-1",
    }

    out = xr.Dataset(
        {"denoise_flags": flags, "background_noise": level},
        coords={"channel": source["channel"], "ping_time": source["ping_time"]},
    )
    out.attrs = {
        PRODUCT_ATTR: MASKED_SV,
        "format_version": FORMAT_VERSION,
        "store_container": store_container or "",
        "sv_source_container": source_container or "",
        "sv_source_path": source_path,
        "sv_source_fingerprint": json.dumps(fingerprint(source)),
        # Keyed by channel label; the threshold applied to each channel.
        "background_snr_db": json.dumps(dict(background_snr or {})),
        "sv_attrs": json.dumps(_jsonable(sv_attrs)),
        "dataset_attrs": json.dumps(_jsonable(dataset_attrs)),
    }

    rechunk = {d: chunks[d] for d in (chunks or {}) if d in out.dims}
    if "range_sample" in out.dims:
        rechunk.setdefault("range_sample", -1)
    out = out.chunk(rechunk or None)
    for var in out.variables.values():
        var.encoding.clear()
    return out


def pruned_view_dataset(
    *, parent: xr.Dataset, kept_ping_times: np.ndarray, parent_container: str | None,
    parent_path: str, store_container: str | None,
) -> xr.Dataset:
    """Assemble the pruned-view store: which of *parent*'s pings survived."""
    import xarray as xr

    keep = np.isin(parent["ping_time"].values, kept_ping_times)
    out = xr.Dataset(
        {"keep_ping": ("ping_time", keep)},
        coords={"ping_time": parent["ping_time"].values},
    )
    out["keep_ping"].attrs["long_name"] = "Ping kept by pruning"
    out.attrs = {
        PRODUCT_ATTR: PRUNED_VIEW,
        "format_version": FORMAT_VERSION,
        "store_container": store_container or "",
        "parent_container": parent_container or "",
        "parent_path": parent_path,
        "parent_ping_count": int(parent.sizes["ping_time"]),
    }
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ── reading ────────────────────────────────────────────────────────────────


def product_kind(ds: xr.Dataset) -> str:
    """``masked_sv``, ``pruned_view``, or ``""`` for an ordinary store."""
    return str(ds.attrs.get(PRODUCT_ATTR, ""))


def resolve_product(ds: xr.Dataset, opener: Opener) -> xr.Dataset:
    """Turn a masked-Sv or pruned-view store into the Sv dataset it stands for.

    *opener* must open ``(zarr_path, container)`` with the same backend that
    opened *ds*, and resolve products itself — that is what lets a pruned view
    stand on a masked store that stands on the Sv source.
    """
    kind = product_kind(ds)
    if kind == MASKED_SV:
        return _resolve_masked(ds, opener)
    if kind == PRUNED_VIEW:
        return _resolve_pruned(ds, opener)
    return ds


def _resolve_masked(store: xr.Dataset, opener: Opener) -> xr.Dataset:
    container = store.attrs.get("sv_source_container") or None
    source = opener(store.attrs["sv_source_path"], container)

    expected = json.loads(store.attrs.get("sv_source_fingerprint", "{}"))
    actual = fingerprint(source)
    if expected and expected != actual:
        raise StaleProductError(
            f"The Sv source {container}/{store.attrs['sv_source_path']} changed after "
            f"these masks were computed (expected {expected}, found {actual}). "
            "Re-run denoising for this day."
        )

    flags = store["denoise_flags"]
    level = store["background_noise"]
    sv = source["Sv"]

    # Background is the only step that changes values. Channels it did not run
    # on have no level, and keep their Sv untouched.
    if bool(np.isfinite(level.values).any()):
        snr = json.loads(store.attrs.get("background_snr_db", "{}"))
        corrected = []
        for label in source["channel"].values:
            ch = {"channel": label}
            ch_level = level.sel(ch)
            if not bool(np.isfinite(ch_level.values).any()):
                corrected.append(sv.sel(ch))
                continue
            corrected.append(
                background_corrected(
                    sv.sel(ch), ch_level, source.sel(ch), snr.get(str(label))
                )
            )
        import xarray as xr

        sv = xr.concat(corrected, dim="channel").transpose(*source["Sv"].dims)

    sv = sv.where(flags == 0)
    sv.attrs = json.loads(store.attrs.get("sv_attrs", "{}"))

    result = source.copy()
    result["Sv"] = sv
    result["noise_mask"] = (flags & MASK_STAGE_BITS) != 0
    result["noise_mask"].attrs["long_name"] = "Cells removed by the mask-based denoisers"
    # The flags and level ride along, carrying the store's provenance, so a
    # later stage can extend them (extend_masked) and a viewer can break the
    # removed cells down by step.
    result["denoise_flags"] = flags.assign_attrs(
        {**flags.attrs, PRODUCT_ATTRS_KEY: json.dumps(dict(store.attrs))}
    )
    result["background_noise"] = level
    extra = json.loads(store.attrs.get("extra_coords", "[]"))
    if extra:
        result = result.assign_coords({name: store[name] for name in extra})
    result.attrs = json.loads(store.attrs.get("dataset_attrs", "{}"))
    return result


def extend_masked(
    resolved: xr.Dataset,
    steps: Mapping[str, xr.DataArray],
    *,
    store_container: str | None,
    dataset_attrs: Mapping[str, Any] | None = None,
    extra_coords: Mapping[str, xr.DataArray] | None = None,
    chunks: Mapping[str, int] | None = None,
) -> xr.Dataset | None:
    """A masked store for *resolved* with more steps' bits set.

    For a stage that removes cells from an already-denoised dataset (seabed
    masking): the new store points at the same Sv source, so it stays a few
    megabytes. Returns ``None`` when *resolved* did not come from a masked
    store — an old full store — and the caller must write data as before.
    """
    if "denoise_flags" not in resolved or PRODUCT_ATTRS_KEY not in resolved["denoise_flags"].attrs:
        return None
    attrs = json.loads(resolved["denoise_flags"].attrs[PRODUCT_ATTRS_KEY])

    flags = resolved["denoise_flags"].values.copy()
    for name, mask in steps.items():
        values = np.asarray(
            mask.broadcast_like(resolved["Sv"]).transpose(*resolved["Sv"].dims).values
        )
        flags[values.astype(bool)] |= np.uint8(FLAG_BITS[name])

    import xarray as xr

    base = resolved["denoise_flags"]
    out = xr.Dataset(
        {
            "denoise_flags": xr.DataArray(
                flags, dims=base.dims, coords=base.coords,
                attrs={k: v for k, v in base.attrs.items() if k != PRODUCT_ATTRS_KEY},
            ),
            "background_noise": resolved["background_noise"],
        }
    )
    for name, coord in (extra_coords or {}).items():
        out[name] = coord.reset_coords(drop=True) if hasattr(coord, "reset_coords") else coord
    out = out.reset_coords(drop=True)
    out.attrs = {
        **attrs,
        "store_container": store_container or "",
        "extra_coords": json.dumps(sorted(extra_coords or {})),
    }
    if dataset_attrs is not None:
        out.attrs["dataset_attrs"] = json.dumps(_jsonable(dataset_attrs))

    rechunk = {d: chunks[d] for d in (chunks or {}) if d in out.dims}
    if "range_sample" in out.dims:
        rechunk.setdefault("range_sample", -1)
    out = out.chunk(rechunk or None)
    for var in out.variables.values():
        var.encoding.clear()
    return out


def _resolve_pruned(store: xr.Dataset, opener: Opener) -> xr.Dataset:
    container = store.attrs.get("parent_container") or None
    parent = opener(store.attrs["parent_path"], container)
    keep = store["keep_ping"].values.astype(bool)
    if parent.sizes["ping_time"] != keep.size or not np.array_equal(
        parent["ping_time"].values, store["ping_time"].values
    ):
        raise StaleProductError(
            f"The parent {container}/{store.attrs['parent_path']} no longer has the pings "
            "this pruned view was computed over. Re-run pruning for this day."
        )
    return parent.isel(ping_time=keep)


def open_product(
    zarr_path: str, container: str | None = None, chunks: dict | None = None
) -> xr.Dataset:
    """Open any product store through the active storage backend, resolved.

    For scripts and notebooks. Pipeline code already gets this from
    ``open_sv_from_azure``.
    """
    from oceanstream.echodata import storage

    kwargs: dict[str, Any] = {"zarr_path": zarr_path, "container": container}
    if chunks is not None:
        kwargs["chunks"] = chunks
    return storage.open_sv_from_azure(**kwargs)


def resolve_at(ds: xr.Dataset, uri: str, open_uri: Callable[[str], xr.Dataset]) -> xr.Dataset:
    """:func:`resolve_product` for a store opened by URI, with the caller's opener.

    For code that addresses stores by URL and opens them its own way (an
    ``az://`` mapper, s3fs, a cache). *open_uri* opens one URI without
    resolving; sources and parents are followed recursively.

    Sources and parents are recorded as ``(container, path)``. Containers sit
    side by side under one root, and each store records its own container, so
    the root is the part of *uri* before ``/{store_container}/``. A writer that
    addresses stores by URL instead records no container and an absolute path.
    """
    if not product_kind(ds):
        return ds
    own = ds.attrs.get("store_container") or ""

    def opener(path: str, container: str | None) -> xr.Dataset:
        if not container:
            target = path
        else:
            marker = f"/{own}/"
            if not own or marker not in f"/{uri}":
                raise ValueError(
                    f"Cannot locate the sources of {uri}: it records container "
                    f"{own!r}, which is not part of its path."
                )
            root = f"/{uri}".split(marker, 1)[0][1:]
            target = f"{root}/{container}/{path.strip('/')}" if root else f"{container}/{path}"
        return resolve_at(open_uri(target), target, open_uri)

    return resolve_product(ds, opener)


def open_product_uri(uri: Any, **open_kwargs: Any) -> xr.Dataset:
    """``xr.open_zarr`` that returns products resolved; a drop-in replacement.

    *uri* is an absolute URI or local path, and *open_kwargs* go to
    ``xr.open_zarr`` for this store and anything it points at. Ordinary stores
    come back exactly as ``xr.open_zarr`` returns them.
    """
    import xarray as xr

    def _open(target: str) -> xr.Dataset:
        ds = xr.open_zarr(target, **open_kwargs)
        if not ds.data_vars and "consolidated" not in open_kwargs:
            ds = xr.open_zarr(target, consolidated=False, **open_kwargs)
        return ds

    target = str(uri).rstrip("/")
    return resolve_at(_open(target), target, _open)
