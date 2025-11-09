from pathlib import Path
import pytest

@pytest.mark.integration
def test_cli_echodata_dry_run(tmp_path: Path):
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
            "echodata",
            "--input-dir",
            str(in_dir),
            "--output-dir",
            str(out_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry run summary" in result.output.lower()
    assert "echodata" in result.output.lower()
