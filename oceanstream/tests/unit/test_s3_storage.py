"""Unit tests for the S3 storage backend used by the batch pipeline.

Covers URI resolution, the container-relative filesystem view, call-signature
compatibility with ``oceanstream.echodata.storage``, and the monkeypatch
contract that the whole backend-swapping scheme depends on.

The MinIO round-trip lives in ``tests/integration/test_s3_storage.py``.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("xarray")
pytest.importorskip("s3fs")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _PROJECT_ROOT / "scripts" / "batch_processing"

#: Top-level module names the batch scripts would shadow (see
#: ``test_denoise_comparison.py`` for the full rationale).
_SHADOWED_MODULES = ("config",)


@contextmanager
def scripts_importable():
    """Make ``scripts/batch_processing`` importable without leaking session state."""
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
    import s3_storage  # noqa: E402


@pytest.fixture
def configured(monkeypatch):
    """Configure the module without touching the real ``storage`` module."""
    monkeypatch.setattr(s3_storage, "_BUCKET", "mybucket")
    monkeypatch.setattr(s3_storage, "_PREFIX", "runs/denoise")
    monkeypatch.setattr(
        s3_storage,
        "_STORAGE_OPTIONS",
        {
            "client_kwargs": {"endpoint_url": "http://localhost:9000/"},
            "key": "minioadmin",
            "secret": "minioadmin",
        },
    )


# ── URI resolution ─────────────────────────────────────────────────────────


class TestUriResolution:
    def test_resolves_container_and_path(self, configured):
        assert (
            s3_storage._resolve("day/x_Sv.zarr", "out")
            == "s3://mybucket/runs/denoise/out/day/x_Sv.zarr"
        )

    def test_container_only(self, configured):
        assert s3_storage._resolve("", "out") == "s3://mybucket/runs/denoise/out"

    def test_path_without_container(self, configured):
        assert s3_storage._resolve("a/b.zarr") == "s3://mybucket/runs/denoise/a/b.zarr"

    def test_empty_prefix_is_not_a_double_slash(self, monkeypatch, configured):
        monkeypatch.setattr(s3_storage, "_PREFIX", "")
        assert s3_storage._resolve("x.zarr", "out") == "s3://mybucket/out/x.zarr"

    def test_leading_and_trailing_slashes_collapse(self, configured):
        assert (
            s3_storage._resolve("/day/x.zarr/", "/out/")
            == "s3://mybucket/runs/denoise/out/day/x.zarr"
        )

    def test_get_zarr_store_uri_matches_resolve(self, configured):
        assert s3_storage.get_zarr_store_uri("d/x.zarr", container="out") == s3_storage._resolve(
            "d/x.zarr", "out"
        )

    def test_unconfigured_bucket_raises(self, monkeypatch):
        monkeypatch.setattr(s3_storage, "_BUCKET", "")
        with pytest.raises(RuntimeError, match="patch_storage"):
            s3_storage._resolve("x.zarr", "out")


class TestStorageOptions:
    def test_shape_matches_echopype_convention(self):
        opts = s3_storage._build_storage_options(
            "http://localhost:9000/", "minioadmin", "minioadmin", None
        )
        assert opts == {
            "client_kwargs": {"endpoint_url": "http://localhost:9000/"},
            "key": "minioadmin",
            "secret": "minioadmin",
        }

    def test_bare_host_endpoint_gets_a_scheme(self):
        opts = s3_storage._build_storage_options("minio.dive.edito.eu", "k", "s", None)
        assert opts["client_kwargs"]["endpoint_url"] == "https://minio.dive.edito.eu"

    def test_region_lands_in_client_kwargs(self):
        opts = s3_storage._build_storage_options(None, "k", "s", "waw3-1")
        assert opts["client_kwargs"] == {"region_name": "waw3-1"}

    def test_falls_back_to_environment(self, monkeypatch):
        monkeypatch.setenv("AWS_S3_ENDPOINT", "https://minio.example/")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "envkey")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "envsecret")
        opts = s3_storage._build_storage_options(None, None, None, None)
        assert opts["key"] == "envkey"
        assert opts["secret"] == "envsecret"
        assert opts["client_kwargs"]["endpoint_url"] == "https://minio.example/"

    def test_no_credentials_leaves_the_default_chain_in_charge(self, monkeypatch):
        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_ENDPOINT",
                    "S3_ENDPOINT_URL", "AWS_DEFAULT_REGION"):
            monkeypatch.delenv(var, raising=False)
        assert s3_storage._build_storage_options(None, None, None, None) == {}

    def test_storage_options_returns_a_copy(self, configured):
        opts = s3_storage.storage_options()
        opts["key"] = "tampered"
        assert s3_storage._STORAGE_OPTIONS["key"] == "minioadmin"


# ── container-relative filesystem view ─────────────────────────────────────


class _FakeFS:
    """Records the absolute paths it is asked for."""

    def __init__(self, entries=None):
        self.entries = entries or []
        self.calls: list[tuple] = []

    def ls(self, path, detail=False, **kwargs):
        self.calls.append(("ls", path))
        if detail:
            return [dict(e) for e in self.entries]
        return [e["name"] for e in self.entries]

    def find(self, path, **kwargs):
        self.calls.append(("find", path))
        return [e["name"] for e in self.entries]

    def put(self, lpath, rpath, recursive=False, **kwargs):
        self.calls.append(("put", lpath, rpath, kwargs))


class TestPrefixFilesystem:
    def _fs(self, entries=None):
        fake = _FakeFS(entries)
        return fake, s3_storage._S3PrefixFS(fake, "mybucket", "runs/denoise")

    def test_ls_takes_container_relative_paths(self):
        fake, fs = self._fs()
        fs.ls("out/2023-10-10")
        assert fake.calls == [("ls", "mybucket/runs/denoise/out/2023-10-10")]

    def test_ls_returns_container_relative_paths(self):
        _, fs = self._fs([{"name": "mybucket/runs/denoise/out/2023-10-10", "type": "directory"}])
        assert fs.ls("out") == ["out/2023-10-10"]

    def test_ls_detail_rewrites_names_but_keeps_type(self):
        _, fs = self._fs(
            [{"name": "mybucket/runs/denoise/out/a.png", "type": "file", "size": 3}]
        )
        assert fs.ls("out", detail=True) == [
            {"name": "out/a.png", "type": "file", "size": 3}
        ]

    def test_missing_directory_lists_empty(self):
        class _Missing(_FakeFS):
            def ls(self, path, detail=False, **kwargs):
                raise FileNotFoundError(path)

        fs = s3_storage._S3PrefixFS(_Missing(), "mybucket", "")
        assert fs.ls("nope") == []

    def test_put_drops_the_azure_only_overwrite_kwarg(self):
        fake, fs = self._fs()
        fs.put("/tmp/a.png", "out/day/a.png", overwrite=True)
        assert fake.calls == [
            ("put", "/tmp/a.png", "mybucket/runs/denoise/out/day/a.png", {})
        ]

    def test_absolute_s3_uri_is_left_alone(self):
        fake, fs = self._fs()
        fs.ls("s3://otherbucket/thing")
        assert fake.calls == [("ls", "otherbucket/thing")]

    def test_empty_prefix_roots_at_the_bucket(self):
        fake = _FakeFS()
        fs = s3_storage._S3PrefixFS(fake, "mybucket", "")
        fs.ls("out")
        assert fake.calls == [("ls", "mybucket/out")]


# ── compatibility with oceanstream.echodata.storage ────────────────────────


#: Every call form the batch pipeline uses against the storage layer.
_CALL_FORMS = [
    ("upload_file_to_blob", ("/tmp/a.png", "day/a.png", "container"), {}),
    ("upload_file_to_blob", ("/tmp/a.png", "day/a.png"), {"container_name": "container"}),
    ("ensure_container_exists", ("container",), {"public_access": "container"}),
    ("open_sv_from_azure", (), {"zarr_path": "d/x.zarr", "container": "c", "chunks": None}),
    ("open_sv_from_azure", ("campaign", "file"), {}),
    ("get_azure_zarr_store", ("d/x.zarr",), {"container": "c", "mode": "w"}),
    ("get_zarr_store_uri", ("",), {"container": "c"}),
    ("save_dataset_to_azure", (None, "d/x.zarr"), {"container": "c"}),
    ("get_azure_filesystem", (), {}),
    ("generate_container_name", ("SD_TPOS2023_v03",), {}),
]


@pytest.mark.parametrize("name, args, kwargs", _CALL_FORMS)
def test_s3_backend_accepts_every_pipeline_call_form(name, args, kwargs):
    inspect.signature(getattr(s3_storage, name)).bind(*args, **kwargs)


@pytest.mark.parametrize("name, args, kwargs", _CALL_FORMS)
def test_original_backend_accepts_the_same_call_forms(name, args, kwargs):
    """Guards against the S3 module drifting away from the Azure original."""
    import oceanstream.echodata.storage as original

    if name == "upload_file_to_blob" and "container_name" in kwargs:
        pytest.skip("original takes container_name positionally only")
    inspect.signature(getattr(original, name)).bind(*args, **kwargs)


def test_get_azure_zarr_store_returns_a_mapper(configured):
    """The campaign append path feeds this straight to ``ds.to_zarr(store)``."""
    import fsspec

    store = s3_storage.get_azure_zarr_store("d/x.zarr", container="out")
    assert isinstance(store, fsspec.FSMap)


def test_get_azure_zarr_store_defaults_to_write_mode():
    assert inspect.signature(s3_storage.get_azure_zarr_store).parameters["mode"].default == "w"


def test_generate_container_name_is_storage_safe():
    name = s3_storage.generate_container_name("SD_TPOS2023_v03")
    assert name == name.lower()
    assert all(c.isalnum() or c == "-" for c in name)
    assert len(name) <= 63


# ── monkeypatch contract ───────────────────────────────────────────────────


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


def test_patch_storage_replaces_every_name(monkeypatch):
    import oceanstream.echodata.storage as mod

    for name in _PATCHED_NAMES:
        monkeypatch.setattr(mod, name, getattr(mod, name))
    monkeypatch.setattr(s3_storage, "_BUCKET", "")
    monkeypatch.setattr(s3_storage, "_PREFIX", "")
    monkeypatch.setattr(s3_storage, "_STORAGE_OPTIONS", {})

    s3_storage.patch_storage("mybucket", "runs", endpoint_url="http://localhost:9000/")

    for name in _PATCHED_NAMES:
        assert getattr(mod, name) is getattr(s3_storage, name), name
    assert s3_storage._BUCKET == "mybucket"
    assert s3_storage._PREFIX == "runs"


def test_patch_storage_requires_a_bucket():
    with pytest.raises(ValueError, match="bucket"):
        s3_storage.patch_storage("")


def test_worker_plugin_does_not_carry_credentials():
    plugin = s3_storage.S3StoragePlugin("mybucket", "runs", endpoint_url="http://x:9000/")
    assert plugin.key is None
    assert plugin.secret is None


# ── the guard the whole scheme rests on ────────────────────────────────────


def _module_level_storage_imports(path: Path) -> list[int]:
    """Line numbers of module-level ``from ...storage import`` statements."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[int] = []

    def walk(body):
        for node in body:
            if isinstance(node, ast.ImportFrom):
                if node.module == "oceanstream.echodata.storage":
                    offenders.append(node.lineno)
            # These still execute at import time; functions and classes do not.
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                walk(node.body)
                walk(getattr(node, "orelse", []))
                walk(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    walk(handler.body)

    walk(tree.body)
    return offenders


def test_no_module_level_storage_imports():
    """``patch_storage`` only works because every consumer imports inside a function.

    A module-level ``from oceanstream.echodata.storage import ...`` binds the
    Azure implementation before the patch runs, and the local/S3 backends
    silently stop being used.
    """
    offenders: dict[str, list[int]] = {}
    for root in (_PROJECT_ROOT / "oceanstream", _SCRIPTS):
        for path in sorted(root.rglob("*.py")):
            lines = _module_level_storage_imports(path)
            if lines:
                offenders[str(path.relative_to(_PROJECT_ROOT))] = lines

    assert not offenders, f"module-level storage imports found: {offenders}"


def _positional_open_sv_calls(path: Path) -> list[int]:
    """Line numbers of ``open_sv_from_azure(<path>, ...)`` calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open_sv_from_azure"
        and node.args
    ]


def test_open_sv_from_azure_is_always_called_by_keyword():
    """The first positional differs between backends, so paths must be keywords.

    ``local_storage`` takes ``zarr_path`` first; the Azure original and the S3
    backend take ``campaign_id``. A positional call silently works locally and
    fails everywhere else.
    """
    offenders: dict[str, list[int]] = {}
    for path in sorted(_SCRIPTS.rglob("*.py")):
        lines = _positional_open_sv_calls(path)
        if lines:
            offenders[str(path.relative_to(_PROJECT_ROOT))] = lines

    assert not offenders, f"positional open_sv_from_azure calls found: {offenders}"
