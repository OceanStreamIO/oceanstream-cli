from pathlib import Path

import pytest


@pytest.mark.integration
def test_cli_multibeam_dry_run(tmp_path: Path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    from typer.testing import CliRunner

    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "multibeam",
            "--input-dir",
            str(in_dir),
            "--output-dir",
            str(out_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry run summary" in result.output.lower()
    assert "multibeam" in result.output.lower()


@pytest.mark.integration
def test_cli_multibeam_real_file(tmp_path: Path):
    """Runs on real .all files when OCEANSTREAM_MULTIBEAM_TEST_DIR points at some."""
    import os

    data = os.environ.get("OCEANSTREAM_MULTIBEAM_TEST_DIR")
    if not data:
        pytest.skip("set OCEANSTREAM_MULTIBEAM_TEST_DIR to a folder of Kongsberg .all files")
    pytest.importorskip("themachinethatgoesping")
    from typer.testing import CliRunner

    from oceanstream import cli as cli_module  # type: ignore

    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        cli_module.app,
        ["process", "multibeam", "--input-dir", data, "--output-dir", str(out_dir),
         "--no-quicklook"],
    )
    assert result.exit_code == 0, result.output
    summaries = list((out_dir / "summaries").glob("*.json"))
    assert summaries
    assert list((out_dir / "soundings").glob("*.parquet"))
