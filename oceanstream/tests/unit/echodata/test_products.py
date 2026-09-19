"""Masked-Sv and pruned-view stores rebuild exactly what the denoiser produced."""

from __future__ import annotations

import json

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")

from oceanstream.echodata import products  # noqa: E402

N_PING = 60
N_RANGE = 80
CHANNELS = ["EKA 1 ES38", "EKA 2 ES200"]


@pytest.fixture
def source() -> xr.Dataset:
    """A two-channel stage-4 Sv dataset with the variables background removal reads."""
    rng = np.random.default_rng(7)
    sv = -80 + 12 * rng.standard_normal((len(CHANNELS), N_PING, N_RANGE))
    sv[:, 3, 10:20] = np.nan  # source NaNs must survive untouched
    echo_range = np.broadcast_to(
        np.linspace(0.2, 120, N_RANGE), (len(CHANNELS), N_PING, N_RANGE)
    ).copy()
    absorption = np.broadcast_to(
        np.array([0.0098, 0.052])[:, None, None], (len(CHANNELS), N_PING, N_RANGE)
    ).copy()
    return xr.Dataset(
        {
            "Sv": (("channel", "ping_time", "range_sample"), sv, {"units": "dB"}),
            "echo_range": (("channel", "ping_time", "range_sample"), echo_range),
            "sound_absorption": (("channel", "ping_time", "range_sample"), absorption),
            "frequency_nominal": ("channel", [38000.0, 200000.0]),
        },
        coords={
            "channel": CHANNELS,
            "ping_time": pd.date_range("2023-10-10", periods=N_PING, freq="2s"),
            "range_sample": np.arange(N_RANGE),
        },
        attrs={"processing_time": "2026-09-01T00:00:00", "processing_level": "Level 2A"},
    )


def _stage_mask(source: xr.Dataset, seed: int, fraction: float) -> xr.DataArray:
    rng = np.random.default_rng(seed)
    mask = xr.DataArray(rng.random(source["Sv"].shape) < fraction, dims=source["Sv"].dims)
    # A stage only ever flags cells that were still finite (build_full_mask).
    return mask & source["Sv"].notnull().values


def _denoise_like_the_pipeline(source: xr.Dataset, snr: float, clip: float):
    """The denoise_day recipe, returning what it used to store and the flag inputs."""
    impulse = _stage_mask(source, 1, 0.02)
    transient = _stage_mask(source, 2, 0.01)
    combined = impulse | transient
    masked = source["Sv"].where(~combined.values)

    # Background runs on the 38 kHz channel only, as a per-channel config may.
    ch0 = source.isel(channel=[0])
    ch0_masked = ch0.assign(Sv=masked.isel(channel=[0]))
    level0 = products.estimate_background_level(ch0_masked, ping_num=10, range_sample_num=8)
    corrected0 = products.background_corrected(ch0_masked["Sv"], level0, ch0_masked, snr)
    after_bg = xr.concat([corrected0, masked.isel(channel=[1])], dim="channel")
    background = masked.notnull() & after_bg.isnull()

    clipped = after_bg.where(after_bg <= clip)
    clip_mask = after_bg.notnull() & clipped.isnull()

    level = xr.concat(
        [level0, xr.full_like(level0, np.nan).assign_coords(channel=[CHANNELS[1]])],
        dim="channel",
    )
    steps = {
        "impulse": impulse,
        "transient": transient,
        "background": background,
        "clip": clip_mask,
    }
    return clipped, steps, level


def _write(ds: xr.Dataset, path) -> None:
    ds.to_zarr(str(path), mode="w")


def _local_opener(root):
    """``(zarr_path, container)`` → resolved dataset, like a storage backend."""

    def opener(path: str, container: str | None) -> xr.Dataset:
        ds = xr.open_zarr(str(root / (container or "") / path))
        return products.resolve_product(ds, opener)

    return opener


def _masked_store(source, steps, level, snr, *, store_container="preset-a"):
    flags = products.build_flags(source["Sv"], steps)
    return products.masked_sv_dataset(
        source=source,
        flags=flags,
        background_level=level,
        background_snr={CHANNELS[0]: snr, CHANNELS[1]: None},
        source_container="shared-sv",
        source_path="2023-10-10/2023-10-10--short_pulse.zarr",
        store_container=store_container,
        sv_attrs={"long_name": "Volume backscattering strength"},
        dataset_attrs={"denoising_applied": True, "denoising_methods": ["impulse"]},
    )


@pytest.fixture
def layout(tmp_path, source):
    """A shared Sv container and a preset container holding masked + pruned stores."""
    snr, clip = 3.0, -60.0
    expected, steps, level = _denoise_like_the_pipeline(source, snr, clip)

    _write(source, tmp_path / "shared-sv/2023-10-10/2023-10-10--short_pulse.zarr")
    _write(
        _masked_store(source, steps, level, snr),
        tmp_path / "preset-a/2023-10-10/2023-10-10--short_pulse--denoised.zarr",
    )
    return tmp_path, expected


class TestBackgroundMatchesEchopype:
    def test_corrected_sv_is_bit_identical(self, source):
        clean = pytest.importorskip("echopype.clean")
        ch = source.isel(channel=[0])
        reference = clean.remove_background_noise(
            ch.copy(), ping_num=10, range_sample_num=8, SNR_threshold="3.0dB"
        )
        level = products.estimate_background_level(ch, ping_num=10, range_sample_num=8)
        ours = products.background_corrected(ch["Sv"], level, ch, 3.0)
        np.testing.assert_array_equal(ours.values, reference["Sv_corrected"].values)

    def test_noise_max_caps_the_level(self, source):
        ch = source.isel(channel=[0])
        level = products.estimate_background_level(
            ch, ping_num=10, range_sample_num=8, background_noise_max=-150.0
        )
        assert float(level.max()) <= -150.0


class TestMaskedSv:
    def test_rebuild_is_exact(self, layout):
        root, expected = layout
        opener = _local_opener(root)
        rebuilt = opener("2023-10-10/2023-10-10--short_pulse--denoised.zarr", "preset-a")
        np.testing.assert_array_equal(
            rebuilt["Sv"].transpose(*expected.dims).values, expected.values
        )

    def test_attrs_and_noise_mask_come_back(self, layout, source):
        root, _ = layout
        rebuilt = _local_opener(root)(
            "2023-10-10/2023-10-10--short_pulse--denoised.zarr", "preset-a"
        )
        assert rebuilt.attrs == {"denoising_applied": True, "denoising_methods": ["impulse"]}
        assert rebuilt["Sv"].attrs == {"long_name": "Volume backscattering strength"}
        assert rebuilt["noise_mask"].dtype == bool
        # The source's other variables ride along unchanged.
        np.testing.assert_array_equal(rebuilt["echo_range"].values, source["echo_range"].values)

    def test_each_step_keeps_its_own_bit(self, source):
        steps = {"impulse": _stage_mask(source, 1, 0.5), "clip": _stage_mask(source, 1, 0.5)}
        flags = products.build_flags(source["Sv"], steps).values
        both = products.FLAG_BITS["impulse"] | products.FLAG_BITS["clip"]
        assert set(np.unique(flags)) <= {0, both}

    def test_store_is_small(self, layout):
        root, _ = layout
        store = root / "preset-a/2023-10-10/2023-10-10--short_pulse--denoised.zarr"
        sv = root / "shared-sv/2023-10-10/2023-10-10--short_pulse.zarr"
        size = lambda p: sum(f.stat().st_size for f in p.rglob("*") if f.is_file())  # noqa: E731
        assert size(store) < size(sv) / 5

    def test_a_recomputed_source_is_refused(self, layout, source):
        root, _ = layout
        moved = source.assign_attrs(processing_time="2026-09-19T00:00:00")
        _write(moved, root / "shared-sv/2023-10-10/2023-10-10--short_pulse.zarr")
        with pytest.raises(products.StaleProductError):
            _local_opener(root)("2023-10-10/2023-10-10--short_pulse--denoised.zarr", "preset-a")

    def test_an_ordinary_store_passes_through(self, source):
        assert products.resolve_product(source, opener=None) is source


class TestPrunedView:
    @pytest.fixture
    def pruned(self, layout):
        root, expected = layout
        opener = _local_opener(root)
        denoised_path = "2023-10-10/2023-10-10--short_pulse--denoised.zarr"
        parent = opener(denoised_path, "preset-a")
        kept = parent["ping_time"].values[::3]
        view = products.pruned_view_dataset(
            parent=parent,
            kept_ping_times=kept,
            parent_container="preset-a",
            parent_path=denoised_path,
            store_container="preset-a",
        )
        _write(view, root / "preset-a/2023-10-10/2023-10-10--short_pulse--pruned.zarr")
        return root, expected, kept

    def test_rebuild_drops_exactly_the_pruned_pings(self, pruned):
        root, expected, kept = pruned
        rebuilt = _local_opener(root)(
            "2023-10-10/2023-10-10--short_pulse--pruned.zarr", "preset-a"
        )
        np.testing.assert_array_equal(rebuilt["ping_time"].values, kept)
        np.testing.assert_array_equal(
            rebuilt["Sv"].transpose(*expected.dims).values,
            expected.sel(ping_time=kept).values,
        )

    def test_uri_opener_follows_both_hops_across_containers(self, pruned):
        root, expected, kept = pruned
        rebuilt = products.open_product_uri(
            str(root / "preset-a/2023-10-10/2023-10-10--short_pulse--pruned.zarr")
        )
        np.testing.assert_array_equal(
            rebuilt["Sv"].transpose(*expected.dims).values,
            expected.sel(ping_time=kept).values,
        )

    def test_a_changed_parent_is_refused(self, pruned, source):
        root, _, _ = pruned
        view_path = root / "preset-a/2023-10-10/2023-10-10--short_pulse--pruned.zarr"
        view = xr.open_zarr(str(view_path)).load()
        view = view.isel(ping_time=slice(1, None)).assign_attrs(view.attrs)
        _write(view, view_path)
        with pytest.raises(products.StaleProductError):
            _local_opener(root)("2023-10-10/2023-10-10--short_pulse--pruned.zarr", "preset-a")


def test_background_thresholds_are_recorded_per_channel(source):
    store = _masked_store(source, {}, None, 3.0)
    assert json.loads(store.attrs["background_snr_db"]) == {CHANNELS[0]: 3.0, CHANNELS[1]: None}
    assert np.isnan(store["background_noise"].values).all()


class TestExtendMasked:
    def test_seabed_adds_a_bit_over_the_same_source(self, layout):
        root, expected = layout
        opener = _local_opener(root)
        denoised = opener("2023-10-10/2023-10-10--short_pulse--denoised.zarr", "preset-a")
        seabed = xr.zeros_like(denoised["Sv"], dtype=bool)
        seabed[:, :, -10:] = True
        seabed = seabed & denoised["Sv"].notnull()
        depth = xr.DataArray(np.full(N_PING, 99.0), dims="ping_time")

        store = products.extend_masked(
            denoised, {"seabed": seabed}, store_container="preset-a",
            extra_coords={"seabed_depth": depth},
        )
        assert store.attrs["sv_source_container"] == "shared-sv"
        _write(store, root / "preset-a/2023-10-10/2023-10-10--short_pulse--masked.zarr")

        masked = opener("2023-10-10/2023-10-10--short_pulse--masked.zarr", "preset-a")
        want = expected.transpose(*masked["Sv"].dims).values.copy()
        want[:, :, -10:] = np.nan
        np.testing.assert_array_equal(masked["Sv"].values, want)
        np.testing.assert_array_equal(masked["seabed_depth"].values, depth.values)
        bit = products.FLAG_BITS["seabed"]
        assert bool(((masked["denoise_flags"] & bit) != 0).any())

    def test_a_plain_dataset_cannot_be_extended(self, source):
        steps = {"seabed": xr.zeros_like(source["Sv"], dtype=bool)}
        assert products.extend_masked(source, steps, store_container="x") is None


def test_absolute_sources_need_no_container(tmp_path, source):
    """A writer that addresses stores by URL records no container at all."""
    sv_uri = str(tmp_path / "anywhere/sv.zarr")
    _write(source, sv_uri)
    store = products.masked_sv_dataset(
        source=source,
        flags=products.build_flags(source["Sv"], {"impulse": _stage_mask(source, 1, 0.1)}),
        background_level=None,
        background_snr=None,
        source_container=None,
        source_path=sv_uri,
        store_container=None,
        sv_attrs={},
        dataset_attrs={},
    )
    denoised_uri = str(tmp_path / "elsewhere/denoised.zarr")
    _write(store, denoised_uri)
    view = products.pruned_view_dataset(
        parent=products.open_product_uri(denoised_uri),
        kept_ping_times=source["ping_time"].values[:10],
        parent_container=None,
        parent_path=denoised_uri,
        store_container=None,
    )
    _write(view, str(tmp_path / "elsewhere/pruned.zarr"))

    rebuilt = products.open_product_uri(str(tmp_path / "elsewhere/pruned.zarr"))
    assert rebuilt.sizes["ping_time"] == 10
    source_nans = int(source["Sv"].isel(ping_time=slice(10)).isnull().sum())
    assert int(rebuilt["Sv"].isnull().sum()) > source_nans
