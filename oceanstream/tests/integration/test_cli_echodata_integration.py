from pathlib import Path
import pytest

@pytest.mark.integration
def test_cli_echodata_convert_dry_run(tmp_path: Path):
    """Test echodata convert command with dry-run flag."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    
    # Create a dummy raw file
    (in_dir / "test.raw").touch()

    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "process",
            "echodata",
            "convert",
            "--input-source",
            str(in_dir),
            "--output-dir",
            str(out_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output.lower()
    assert "echodata" in result.output.lower()


@pytest.mark.integration
def test_cli_echodata_convert_no_files(tmp_path: Path):
    """Test echodata convert command with empty directory."""
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
            "convert",
            "--input-source",
            str(in_dir),
            "--output-dir",
            str(out_dir),
        ],
    )
    # Should exit with error when no raw files found
    assert result.exit_code == 1, result.output
    assert "no .raw files" in result.output.lower()


@pytest.mark.integration
def test_cli_echodata_help():
    """Test echodata help output."""
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["process", "echodata", "--help"],
    )
    assert result.exit_code == 0, result.output
    # Should list subcommands
    assert "convert" in result.output.lower()
    assert "compute-sv" in result.output.lower() or "sv" in result.output.lower()


@pytest.mark.integration
def test_cli_echodata_compute_sv_help():
    """Test echodata compute-sv help output."""
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["process", "echodata", "compute-sv", "--help"],
    )
    assert result.exit_code == 0, result.output
    assert "input-source" in result.output.lower()


@pytest.mark.integration
def test_cli_echodata_denoise_help():
    """Test echodata denoise help output."""
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["process", "echodata", "denoise", "--help"],
    )
    assert result.exit_code == 0, result.output
    assert "methods" in result.output.lower()


@pytest.mark.integration
def test_cli_echodata_compute_mvbs_help():
    """Test echodata compute-mvbs help output."""
    from typer.testing import CliRunner
    from oceanstream import cli as cli_module  # type: ignore

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["process", "echodata", "compute-mvbs", "--help"],
    )
    assert result.exit_code == 0, result.output
    assert "range-bin" in result.output.lower()
