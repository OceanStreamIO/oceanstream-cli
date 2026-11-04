"""The `oceanstream hpc` commands, without a cluster."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from oceanstream import cli as cli_module

from .conftest import SITE
from .test_staging_cruise import RUN_TOML

runner = CliRunner()


def _env(tmp_path: Path) -> dict[str, str]:
    key = tmp_path / "id_test"
    key.write_text("not a key")
    return {**SITE, "HPC_SSH_KEY": str(key), "HPC_SITE_FILE": str(tmp_path / "none.env"),
            "OCEANSTREAM_NO_MCP_PROMPT": "1"}


def test_submit_render_only_prints_the_node_job(tmp_path: Path) -> None:
    d = tmp_path / "campaigns" / "SE2204"
    d.mkdir(parents=True)
    (d / "hpc-run.toml").write_text(RUN_TOML)
    (d / "days.txt").write_text("2022-06-21\n2022-06-22\n")
    result = runner.invoke(cli_module.app, ["hpc", "submit", "SE2204", "--render-only",
                                            "--campaigns-dir", str(tmp_path / "campaigns")],
                           env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "#SBATCH --exclusive" in result.output
    assert "run-node" in result.output and "--encode-mode power" in result.output


def test_unknown_campaign_says_how_to_create_it(tmp_path: Path) -> None:
    result = runner.invoke(cli_module.app, ["hpc", "submit", "NOPE", "--render-only",
                                            "--campaigns-dir", str(tmp_path)],
                           env=_env(tmp_path))
    assert result.exit_code == 1
    assert "oceanstream campaign create NOPE" in result.output


def test_site_reports_missing_settings(tmp_path: Path) -> None:
    env = {**_env(tmp_path), "HPC_XFER_HOST": ""}
    result = runner.invoke(cli_module.app, ["hpc", "site"], env=env)
    assert result.exit_code == 1
    assert "HPC_XFER_HOST" in result.output


def test_module_entry_point_needs_only_the_hpc_package() -> None:
    out = subprocess.run([sys.executable, "-m", "oceanstream.hpc", "--help"],
                         capture_output=True, text=True, check=True).stdout
    assert "run-day" in out and "run-cruise" in out
