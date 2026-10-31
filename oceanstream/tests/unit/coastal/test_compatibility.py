"""The old import and command paths delegate to CoastObserve."""

import importlib
import pickle
import subprocess
import sys

import coastobserve
from typer.testing import CliRunner


def test_public_classes_keep_identity_and_pickle_support():
    import oceanstream.coastal as legacy

    assert legacy.RetrievalConfig is coastobserve.RetrievalConfig
    value = legacy.RetrievalConfig()
    assert type(pickle.loads(pickle.dumps(value))) is coastobserve.RetrievalConfig


def test_nested_imports_share_modules_and_monkeypatches(monkeypatch):
    for suffix in ("optics.water", "inversion.lee", "bathymetry.tide", "io.rasters", "aois"):
        legacy = importlib.import_module("oceanstream.coastal." + suffix)
        canonical = importlib.import_module("coastobserve." + suffix)
        assert legacy is canonical
    sentinel = object()
    monkeypatch.setattr("oceanstream.coastal.optics.water.a_water", sentinel)
    assert importlib.import_module("coastobserve.optics.water").a_water is sentinel


def test_legacy_cli_delegates_to_canonical_commands():
    from oceanstream.cli import app
    from coastobserve.cli import app as standalone

    runner = CliRunner()
    old = runner.invoke(app, ["process", "coastal", "detect", "--help"])
    new = runner.invoke(standalone, ["detect", "--help"])
    assert old.exit_code == new.exit_code == 0
    assert "--fail-on-reject" in old.output
    assert "--fail-on-reject" in new.output


def test_legacy_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "oceanstream.coastal.quickstart", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--output" in result.stdout
