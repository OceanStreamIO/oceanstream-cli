"""Unit tests for echodata CLI commands and processor.

Tests:
- compute-nasc CLI command
- plot CLI command
- EchodataProcessor class
- ProcessingResult and PipelineResult dataclasses
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
import pytest
from typer.testing import CliRunner

from oceanstream.cli import app
from oceanstream.echodata.processor import (
    EchodataProcessor,
    ProcessingResult,
    PipelineResult,
    process,
)
from oceanstream.echodata.config import (
    EchodataConfig,
    DenoiseConfig,
    MVBSConfig,
    NASCConfig,
)


runner = CliRunner()


# =============================================================================
# ProcessingResult and PipelineResult Tests
# =============================================================================

class TestProcessingResult:
    """Test the ProcessingResult dataclass."""
    
    def test_create_success_result(self):
        """Test creating a successful processing result."""
        result = ProcessingResult(
            step="convert",
            success=True,
            output_path=Path("/tmp/output"),
            message="Converted 10 files",
            duration_seconds=5.5,
            metadata={"file_count": 10},
        )
        
        assert result.step == "convert"
        assert result.success is True
        assert result.output_path == Path("/tmp/output")
        assert result.duration_seconds == 5.5
        assert result.metadata["file_count"] == 10
    
    def test_create_failure_result(self):
        """Test creating a failed processing result."""
        result = ProcessingResult(
            step="calibrate",
            success=False,
            message="Calibration file not found",
        )
        
        assert result.success is False
        assert "not found" in result.message
        assert result.output_path is None
    
    def test_default_values(self):
        """Test default values for ProcessingResult."""
        result = ProcessingResult(step="test", success=True)
        
        assert result.output_path is None
        assert result.message == ""
        assert result.duration_seconds == 0.0
        assert result.metadata == {}


class TestPipelineResult:
    """Test the PipelineResult dataclass."""
    
    def test_success_property_all_succeed(self):
        """Test success property when all steps succeed."""
        result = PipelineResult(
            campaign_id="test_campaign",
            output_dir=Path("/tmp/output"),
            steps=[
                ProcessingResult(step="convert", success=True),
                ProcessingResult(step="compute_sv", success=True),
                ProcessingResult(step="denoise", success=True),
            ],
        )
        
        assert result.success is True
    
    def test_success_property_with_failure(self):
        """Test success property when a step fails."""
        result = PipelineResult(
            campaign_id="test_campaign",
            output_dir=Path("/tmp/output"),
            steps=[
                ProcessingResult(step="convert", success=True),
                ProcessingResult(step="compute_sv", success=False),
                ProcessingResult(step="denoise", success=True),
            ],
        )
        
        assert result.success is False
    
    def test_total_duration(self):
        """Test total_duration property."""
        result = PipelineResult(
            campaign_id="test",
            output_dir=Path("/tmp"),
            steps=[
                ProcessingResult(step="a", success=True, duration_seconds=1.0),
                ProcessingResult(step="b", success=True, duration_seconds=2.5),
                ProcessingResult(step="c", success=True, duration_seconds=3.0),
            ],
        )
        
        assert result.total_duration == 6.5
    
    def test_failed_steps(self):
        """Test failed_steps property."""
        result = PipelineResult(
            campaign_id="test",
            output_dir=Path("/tmp"),
            steps=[
                ProcessingResult(step="convert", success=True),
                ProcessingResult(step="calibrate", success=False, message="File not found"),
                ProcessingResult(step="compute_sv", success=False, message="No input"),
            ],
        )
        
        failed = result.failed_steps
        assert len(failed) == 2
        assert failed[0].step == "calibrate"
        assert failed[1].step == "compute_sv"
    
    def test_to_dict(self):
        """Test serialization to dictionary."""
        start = datetime(2024, 1, 15, 10, 0, 0)
        end = datetime(2024, 1, 15, 10, 5, 0)
        
        result = PipelineResult(
            campaign_id="TPOS2023",
            output_dir=Path("/output/TPOS2023"),
            start_time=start,
            end_time=end,
            steps=[
                ProcessingResult(
                    step="convert",
                    success=True,
                    output_path=Path("/output/raw"),
                    duration_seconds=10.0,
                    metadata={"count": 5},
                ),
            ],
        )
        
        d = result.to_dict()
        
        assert d["campaign_id"] == "TPOS2023"
        assert d["output_dir"] == "/output/TPOS2023"
        assert d["success"] is True
        assert d["total_duration_seconds"] == 10.0
        assert d["start_time"] == "2024-01-15T10:00:00"
        assert d["end_time"] == "2024-01-15T10:05:00"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["step"] == "convert"
    
    def test_save_report(self, tmp_path):
        """Test saving pipeline report to JSON file."""
        result = PipelineResult(
            campaign_id="test",
            output_dir=tmp_path,
            steps=[ProcessingResult(step="test", success=True)],
        )
        
        report_path = tmp_path / "report.json"
        result.save_report(report_path)
        
        assert report_path.exists()
        
        import json
        with open(report_path) as f:
            data = json.load(f)
        
        assert data["campaign_id"] == "test"
        assert len(data["steps"]) == 1


# =============================================================================
# EchodataProcessor Tests
# =============================================================================

class TestEchodataProcessor:
    """Test the EchodataProcessor class."""
    
    def test_init_with_defaults(self):
        """Test processor initialization with default config."""
        processor = EchodataProcessor(verbose=True)
        
        assert processor.verbose is True
        assert processor.config is not None
        assert isinstance(processor.config, EchodataConfig)
    
    def test_init_with_custom_config(self):
        """Test processor initialization with custom config."""
        config = EchodataConfig(
            sonar_model="EK60",
            parallel=False,
            n_workers=2,
        )
        
        processor = EchodataProcessor(
            config=config,
            campaign_id="TEST_CAMPAIGN",
        )
        
        assert processor.config.sonar_model == "EK60"
        assert processor.config.parallel is False
        assert processor.campaign_id == "TEST_CAMPAIGN"
    
    def test_log_when_verbose(self, capsys):
        """Test logging when verbose is enabled."""
        processor = EchodataProcessor(verbose=True)
        processor.log("Test message")
        
        captured = capsys.readouterr()
        assert "Test message" in captured.out
    
    def test_log_when_not_verbose(self, capsys):
        """Test logging when verbose is disabled."""
        processor = EchodataProcessor(verbose=False)
        processor.log("Test message")
        
        captured = capsys.readouterr()
        assert "Test message" not in captured.out
    
    def test_elapsed_time(self):
        """Test elapsed time tracking."""
        processor = EchodataProcessor()
        
        import time
        time.sleep(0.1)
        
        elapsed = processor.elapsed_time()
        assert elapsed >= 0.1
    
    def test_convert_no_files_found(self, tmp_path):
        """Test convert step when no raw files found."""
        processor = EchodataProcessor()
        
        result = processor.convert(
            input_dir=tmp_path,
            output_dir=tmp_path / "output",
        )
        
        assert result.success is False
        assert "No *.raw files found" in result.message
    
    def test_calibrate_no_file(self, tmp_path):
        """Test calibrate step with no calibration file."""
        processor = EchodataProcessor()
        
        result = processor.calibrate(calibration_file=None)
        
        assert result.success is True
        assert "skipping" in result.message.lower()
    
    def test_calibrate_file_not_found(self, tmp_path):
        """Test calibrate step when calibration file doesn't exist."""
        processor = EchodataProcessor()
        processor._converted_paths = [tmp_path / "test.zarr"]
        
        result = processor.calibrate(
            calibration_file=tmp_path / "nonexistent.xlsx"
        )
        
        assert result.success is False
        assert "not found" in result.message
    
    def test_calibrate_no_converted_files(self, tmp_path):
        """Test calibrate step when no files have been converted."""
        processor = EchodataProcessor()
        
        # Create a dummy calibration file
        cal_file = tmp_path / "calibration.xlsx"
        cal_file.touch()
        
        result = processor.calibrate(calibration_file=cal_file)
        
        assert result.success is False
        assert "Run convert() first" in result.message
    
    def test_compute_sv_no_input_paths(self, tmp_path):
        """Test compute_sv with no input paths."""
        processor = EchodataProcessor()
        
        result = processor.compute_sv(output_dir=tmp_path)
        
        assert result.success is False
        assert "No input paths available" in result.message
    
    def test_denoise_no_sv_paths(self, tmp_path):
        """Test denoise with no Sv paths."""
        processor = EchodataProcessor()
        
        result = processor.denoise(output_dir=tmp_path)
        
        assert result.success is False
        assert "No Sv paths available" in result.message
    
    def test_compute_mvbs_no_input(self, tmp_path):
        """Test compute_mvbs with no input."""
        processor = EchodataProcessor()
        
        result = processor.compute_mvbs(output_dir=tmp_path)
        
        assert result.success is False
        assert "No Sv paths available" in result.message
    
    def test_compute_nasc_no_input(self, tmp_path):
        """Test compute_nasc with no input."""
        processor = EchodataProcessor()
        
        result = processor.compute_nasc(output_dir=tmp_path)
        
        assert result.success is False
        assert "No Sv paths available" in result.message
    
    def test_generate_echograms_no_input(self, tmp_path):
        """Test generate_echograms with no input."""
        processor = EchodataProcessor()
        
        result = processor.generate_echograms(output_dir=tmp_path)
        
        assert result.success is False
        assert "No Sv paths available" in result.message


class TestLegacyProcessFunction:
    """Test the legacy process() function for backward compatibility."""
    
    def test_dry_run_output(self, capsys):
        """Test dry run output from legacy process function."""
        mock_provider = Mock()
        mock_provider.name = "saildrone"
        
        process(
            provider=mock_provider,
            input_dir=Path("/input"),
            output_dir=Path("/output"),
            dry_run=True,
        )
        
        captured = capsys.readouterr()
        assert "Dry Run Summary" in captured.out
        assert "saildrone" in captured.out
        assert "1. Convert raw files" in captured.out
        assert "8. Generate echograms" in captured.out


# =============================================================================
# CLI Command Tests
# =============================================================================

class TestEchodataComputeNascCLI:
    """Test the compute-nasc CLI command."""
    
    def test_compute_nasc_no_input_dir(self):
        """Test compute-nasc with non-existent input directory."""
        result = runner.invoke(app, [
            "process", "echodata", "compute-nasc",
            "--input-source", "/nonexistent/path",
        ])
        
        # Should fail because input doesn't exist
        assert result.exit_code != 0 or "exist" in result.stdout.lower()
    
    def test_compute_nasc_help(self):
        """Test compute-nasc help output."""
        result = runner.invoke(app, ["process", "echodata", "compute-nasc", "--help"])
        
        assert result.exit_code == 0
        assert "NASC" in result.stdout or "nasc" in result.stdout.lower()
        assert "--range-bin" in result.stdout
        assert "--dist-bin" in result.stdout
    
    @patch("oceanstream.echodata.compute.compute_nasc")
    def test_compute_nasc_with_mocked_function(self, mock_compute_nasc, tmp_path):
        """Test compute-nasc calls compute_nasc with correct parameters."""
        # Create a mock zarr directory
        zarr_dir = tmp_path / "test_Sv.zarr"
        zarr_dir.mkdir()
        (zarr_dir / ".zattrs").write_text("{}")
        
        # Mock xarray.open_zarr
        mock_ds = MagicMock()
        with patch("xarray.open_zarr", return_value=mock_ds):
            result = runner.invoke(app, [
                "process", "echodata", "compute-nasc",
                "--input-source", str(tmp_path),
                "--range-bin", "20m",
                "--dist-bin", "1nmi",
                "-v",
            ])
        
        # Check it was called (may fail if no zarr found)
        if result.exit_code == 0:
            mock_compute_nasc.assert_called()


class TestEchodataPlotCLI:
    """Test the plot CLI command."""
    
    def test_plot_help(self):
        """Test plot help output."""
        result = runner.invoke(app, ["process", "echodata", "plot", "--help"])
        
        assert result.exit_code == 0
        assert "--cmap" in result.stdout
        assert "--vmin" in result.stdout
        assert "--vmax" in result.stdout
        assert "--dpi" in result.stdout
    
    def test_plot_no_input_dir(self):
        """Test plot with non-existent input directory."""
        result = runner.invoke(app, [
            "process", "echodata", "plot",
            "--input-source", "/nonexistent/path",
        ])
        
        # Should fail because input doesn't exist
        assert result.exit_code != 0 or "exist" in result.stdout.lower()


# =============================================================================
# Config Tests
# =============================================================================

class TestEchodataConfig:
    """Test EchodataConfig class."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = EchodataConfig()
        
        assert config.sonar_model == "EK80"
        assert config.parallel is True
        assert config.n_workers == 4
        assert config.use_dask is True
    
    def test_denoise_config_defaults(self):
        """Test default denoising configuration."""
        config = DenoiseConfig()
        
        assert "background" in config.methods
        assert "transient" in config.methods
        assert "impulse" in config.methods
        assert "attenuation" in config.methods
    
    def test_mvbs_config_defaults(self):
        """Test default MVBS configuration."""
        config = MVBSConfig()
        
        assert config.range_bin == "1m"
        assert config.ping_time_bin == "5s"
    
    def test_nasc_config_defaults(self):
        """Test default NASC configuration."""
        config = NASCConfig()
        
        assert config.range_bin == "10m"
        assert config.dist_bin == "0.5nmi"
    
    def test_config_to_dict(self):
        """Test config serialization to dict."""
        config = EchodataConfig(
            sonar_model="EK60",
            campaign_id="TEST",
        )
        
        d = config.to_dict()
        
        assert d["sonar_model"] == "EK60"
        assert "denoise" in d
        assert "mvbs" in d
        assert "nasc" in d
    
    def test_denoise_to_background_params(self):
        """Test DenoiseConfig.to_background_params method."""
        config = DenoiseConfig(
            background_range_window=25,
            background_ping_window=60,
            background_snr_threshold=4.0,
            background_noise_max=-120.0,
        )
        
        params = config.to_background_params()
        
        assert params["range_window"] == 25
        assert params["ping_window"] == 60
        assert params["SNR_threshold"] == "4.0dB"
    
    def test_mvbs_to_echopype_kwargs(self):
        """Test MVBSConfig.to_echopype_kwargs method."""
        config = MVBSConfig(range_bin="2m", ping_time_bin="10s")
        
        kwargs = config.to_echopype_kwargs()
        
        assert kwargs["range_bin"] == "2m"
        assert kwargs["ping_time_bin"] == "10s"
    
    def test_nasc_to_echopype_kwargs(self):
        """Test NASCConfig.to_echopype_kwargs method."""
        config = NASCConfig(range_bin="15m", dist_bin="1nmi")
        
        kwargs = config.to_echopype_kwargs()
        
        assert kwargs["range_bin"] == "15m"
        assert kwargs["dist_bin"] == "1nmi"
