"""MinIO-backed round-trip tests for the S3 storage backend.

Start the harness first::

    docker compose -f .ci_helpers/docker/docker-compose.yaml up -d
    pytest -m integration oceanstream/tests/integration/test_s3_storage.py

The service definition and the credentials below are echopype's — see
``echopype/tests/conftest.py`` (``minio_bucket``) — so this exercises the same
zarr-v3-over-s3fs path that echopype already runs in CI.
"""

from __future__ import annotations

import os
import socket
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("xarray")
pytest.importorskip("s3fs")

import xarray as xr  # noqa: E402

pytestmark = pytest.mark.integration

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _PROJECT_ROOT / "scripts" / "batch_processing"
_SHADOWED_MODULES = ("config",)

_PATCHED_NAMES = (
    "save_dataset_to_azure",
    "open_sv_from_azure",
    "get_azure_zarr_store",
    "get_azure_filesystem",
    "ensure_container_exists",
    "generate_container_name",
    "upload_file_to_blob",
    "get_zarr_store_uri",
)


@contextmanager
def scripts_importable():
    env_snapshot = dict(os.environ)
    path_snapshot = list(sys.path)
    shadowed = {name: sys.modules.get(name) for name in _SHADOWED_MODULES}
    for name in _SHADOWED_MODULES:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(_SCRIPTS))
    try:
        yield
    finally:
        sys.path[:] = path_snapshot
        for name, module in shadowed.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        os.environ.clear()
        os.environ.update(env_snapshot)


with scripts_importable():
    import experiment_contract  # noqa: E402
    import local_storage  # noqa: E402
    import s3_storage  # noqa: E402


@pytest.fixture(scope="session")
def minio_bucket():
    """echopype's canonical storage_options shape."""
    return dict(
        client_kwargs=dict(endpoint_url="http://localhost:9000/"),
        key="minioadmin",
        secret="minioadmin",
    )


@pytest.fixture(scope="session")
def minio_available():
    try:
        with socket.create_connection(("localhost", 9000), timeout=2):
            return True
    except OSError:
        pytest.skip(
            "MinIO is not reachable on localhost:9000 — start it with "
            "`docker compose -f .ci_helpers/docker/docker-compose.yaml up -d`"
        )


@pytest.fixture
def restore_storage():
    """Undo the monkeypatch so backend swaps don't leak between tests."""
    import oceanstream.echodata.storage as mod

    saved = {name: getattr(mod, name) for name in _PATCHED_NAMES}
    yield mod
    for name, fn in saved.items():
        setattr(mod, name, fn)


@pytest.fixture
def s3_backend(minio_available, minio_bucket, restore_storage):
    """Patch storage to a throwaway bucket and hand back the module."""
    bucket = f"oceanstream-test-{uuid.uuid4().hex[:8]}"
    s3_storage.patch_storage(
        bucket,
        "runs/denoise",
        endpoint_url=minio_bucket["client_kwargs"]["endpoint_url"],
        key=minio_bucket["key"],
        secret=minio_bucket["secret"],
    )
    s3_storage.ensure_container_exists("out")
    yield s3_storage
    fs = s3_storage._filesystem()
    try:
        fs.rm(bucket, recursive=True)
    except Exception:
        pass


def _sv_dataset(seed: int = 0) -> xr.Dataset:
    """Small synthetic Sv dataset with the pipeline's dimension names."""
    rng = np.random.default_rng(seed)
    sv = rng.normal(-70.0, 5.0, size=(2, 24, 16)).astype("float32")
    return xr.Dataset(
        {"Sv": (("channel", "ping_time", "range_sample"), sv)},
        coords={
            "channel": ["chan-38k", "chan-200k"],
            "ping_time": np.arange("2023-10-10", "2023-10-10T00:00:24", dtype="datetime64[s]"),
            "range_sample": np.arange(16),
        },
    )


# ── round trip ─────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_save_then_open(self, s3_backend):
        ds = _sv_dataset()
        uri = s3_backend.save_dataset_to_azure(ds, "2023-10-10/x_Sv.zarr", container="out")
        assert uri.startswith("s3://")

        loaded = s3_backend.open_sv_from_azure(
            zarr_path="2023-10-10/x_Sv.zarr", container="out", chunks=None
        )
        xr.testing.assert_allclose(loaded.Sv, ds.Sv)

    def test_overwrite_removes_stale_keys(self, s3_backend):
        wide = _sv_dataset()
        s3_backend.save_dataset_to_azure(wide, "2023-10-10/y_Sv.zarr", container="out")
        narrow = wide.isel(range_sample=slice(0, 4))
        s3_backend.save_dataset_to_azure(narrow, "2023-10-10/y_Sv.zarr", container="out")

        loaded = s3_backend.open_sv_from_azure(
            zarr_path="2023-10-10/y_Sv.zarr", container="out", chunks=None
        )
        assert loaded.sizes["range_sample"] == 4

    def test_mapper_supports_the_campaign_append_path(self, s3_backend):
        """``process_campaign`` writes campaign zarrs through the FSMap."""
        ds = _sv_dataset()
        store = s3_backend.get_azure_zarr_store("campaign.zarr", container="out", mode="w")
        ds.to_zarr(store, mode="w")

        store = s3_backend.get_azure_zarr_store("campaign.zarr", container="out", mode="a")
        ds.to_zarr(store, append_dim="ping_time", safe_chunks=False)

        loaded = s3_backend.open_sv_from_azure(
            zarr_path="campaign.zarr", container="out", chunks=None
        )
        assert loaded.sizes["ping_time"] == 2 * ds.sizes["ping_time"]

    def test_upload_and_list(self, s3_backend, tmp_path):
        png = tmp_path / "2023-10-10--short_pulse--38kHz.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        s3_backend.upload_file_to_blob(
            str(png), f"2023-10-10/denoised/{png.name}", container="out"
        )

        fs = s3_backend.get_azure_filesystem()
        assert fs.ls("out/2023-10-10/denoised") == [f"out/2023-10-10/denoised/{png.name}"]
        assert fs.exists(f"out/2023-10-10/denoised/{png.name}")

    def test_upload_accepts_the_container_name_keyword(self, s3_backend, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        s3_backend.upload_file_to_blob(str(f), "misc/a.txt", container_name="out")
        assert s3_backend.get_azure_filesystem().exists("out/misc/a.txt")


class TestEchogramCounting:
    """``_count_existing_echograms`` must see S3 uploads, not just Azure blobs."""

    def test_counts_nested_and_legacy_layouts(self, s3_backend, tmp_path):
        png = tmp_path / "p.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        day, cat = "2023-10-10", "short_pulse"
        for blob in (
            f"{day}/denoised/{day}--{cat}--38kHz.png",
            f"{day}/mvbs/{day}--{cat}--200kHz.png",
            f"{day}/{day}--{cat}--legacy.png",
            f"{day}/{cat}--mvbs-legacy.png",
            f"{day}/denoised/{day}--long_pulse--38kHz.png",
        ):
            s3_backend.upload_file_to_blob(str(png), blob, container="out")

        with scripts_importable():
            import process_campaign

        assert process_campaign._count_existing_echograms("out", day, cat) == 4


# ── parity with the local backend ──────────────────────────────────────────


def test_s3_and_local_backends_produce_identical_zarr(
    s3_backend, tmp_path, restore_storage
):
    """Same input, same bytes — the backend must not change the product."""
    zarr_rel = "2023-10-10/parity_Sv.zarr"

    s3_backend.save_dataset_to_azure(_sv_dataset(seed=7), zarr_rel, container="out")
    downloaded = tmp_path / "from_s3"
    s3_backend._filesystem().get(
        s3_backend._resolve(zarr_rel, "out"), str(downloaded), recursive=True
    )

    local_root = tmp_path / "local_out"
    local_storage.patch_storage(local_root)
    local_path = Path(
        local_storage.save_dataset_to_azure(_sv_dataset(seed=7), zarr_rel, container="out")
    )

    assert (
        experiment_contract.fingerprint_tree(downloaded)["sha256"]
        == experiment_contract.fingerprint_tree(local_path)["sha256"]
    )
