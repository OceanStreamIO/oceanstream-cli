"""The surface exclusion before MVBS and NASC masks samples, never positions or channels."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")
pytest.importorskip("echopype")

from oceanstream.echodata import storage  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "batch_processing"


@contextmanager
def scripts_importable():
    """Import ``scripts/batch_processing`` without leaking ``config`` or the environment."""
    env, path, shadowed = dict(os.environ), list(sys.path), sys.modules.pop("config", None)
    sys.path.insert(0, str(_SCRIPTS))
    try:
        yield
    finally:
        sys.path[:] = path
        sys.modules.pop("config", None)
        if shadowed is not None:
            sys.modules["config"] = shadowed
        os.environ.clear()
        os.environ.update(env)


with scripts_importable():
    import process_campaign as pc  # noqa: E402

#: A layer from 5 m, below the 1.9 m exclusion: every ping's top samples lose their depth.
LAYERS = {38000: {"start_depth_m": 5.0}}
SV = -60.0


def _day(n_pings=40, n_range=30):
    """Denoised 38 kHz Sv on a 1 m grid from 0 m, the platform steaming north."""
    shape = (1, n_pings, n_range)
    depth = np.broadcast_to(np.arange(n_range, dtype=float), shape).copy()
    grid = ("channel", "ping_time", "range_sample")
    return xr.Dataset(
        {
            "Sv": (grid, np.full(shape, SV)),
            "echo_range": (grid, depth.copy()),
            "depth": (grid, depth),
            "denoise_flags": (grid, np.zeros(shape, np.uint8)),
            "frequency_nominal": ("channel", [38000.0]),
            # 0.002 degrees a ping: 40 pings cover 4.8 nmi, ten 0.5 nmi bins.
            "latitude": ("ping_time", np.arange(n_pings) * 0.002),
            "longitude": ("ping_time", -140.0 + np.arange(n_pings) * 0.0005),
        },
        coords={
            "channel": ["38"],
            "ping_time": pd.date_range("2023-10-02", periods=n_pings, freq="1min"),
            "range_sample": np.arange(n_range),
        },
    )


@pytest.fixture
def stored(monkeypatch):
    """The day served by the storage functions; whatever is saved lands in the dict."""
    saved: dict[str, xr.Dataset] = {}
    monkeypatch.setattr(storage, "open_sv_from_azure", lambda zarr_path, container: _day())
    monkeypatch.setattr(
        storage,
        "save_dataset_to_azure",
        lambda ds, zarr_path, container: saved.__setitem__(zarr_path, ds.load()),
    )
    return saved


def _args(**extra):
    return dict(
        zarr_path="2023-10-02/2023-10-02--short_pulse--pruned.zarr",
        output_container="test",
        chunks={},
        day_key="2023-10-02",
        category="short_pulse",
        cruise_id="TEST",
        surface_exclusion_depth=1.9,
        integration_layers=LAYERS,
        **extra,
    )


class TestExcludeSurface:
    def test_samples_above_the_depth_are_masked(self):
        out = pc._exclude_surface(_day(), 1.9)
        assert np.isnan(out["Sv"].values[0, :, :2]).all()
        assert (out["Sv"].values[0, :, 2:] == SV).all()
        assert np.isnan(out["depth"].values[0, :, :2]).all()

    def test_positions_and_channels_keep_their_shape(self):
        ds = _day()
        out = pc._exclude_surface(ds, 1.9)
        for name in ("latitude", "longitude", "frequency_nominal"):
            assert out[name].dims == ds[name].dims
            np.testing.assert_array_equal(out[name].values, ds[name].values)


class TestPositionsSurviveTheLayer:
    """With the layer starting below the exclusion, the top sample of every ping has no
    depth; spread over the samples, the positions and frequencies were NaN everywhere."""

    def test_nasc(self, stored):
        path = pc.compute_nasc_day(range_bin="10m", dist_bin="0.5nmi", **_args())
        nasc = stored[path]
        assert nasc.sizes["distance"] > 1
        np.testing.assert_array_equal(nasc["frequency_nominal"].values, [38000.0])
        assert np.isfinite(nasc["latitude"].values).all()
        assert (nasc["NASC"].sum("depth").values > 0).all()

    def test_mvbs(self, stored):
        path = pc.compute_mvbs_day(range_bin="1m", ping_time_bin="5min", **_args())
        mvbs = stored[path]
        np.testing.assert_array_equal(mvbs["frequency_nominal"].values, [38000.0])
        assert np.isfinite(mvbs["latitude"].values).all()
        assert np.isfinite(mvbs["longitude"].values).all()
