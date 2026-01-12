"""E2E tests for raw file conversion."""

from pathlib import Path
import pytest


@pytest.mark.e2e
class TestConvertRawE2E:
    """End-to-end tests for converting EK80 raw files."""

    def test_convert_single_file(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Convert a single raw file to zarr using our convert module."""
        from oceanstream.echodata.convert import convert_raw_file
        
        output_path = convert_raw_file(
            sample_raw_file,
            tmp_path,
            sonar_model="EK80",
        )
        
        assert output_path.exists(), f"Output zarr not created: {output_path}"
        assert output_path.suffix == ".zarr"
        
        # Verify we can reload it
        import echopype as ep
        
        echodata = ep.open_converted(output_path)
        assert echodata is not None
        assert echodata.sonar is not None

    def test_convert_preserves_metadata(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Converted zarr should preserve essential metadata."""
        from oceanstream.echodata.convert import convert_raw_file
        import echopype as ep
        
        output_path = convert_raw_file(sample_raw_file, tmp_path, sonar_model="EK80")
        echodata = ep.open_converted(output_path)
        
        # Check essential groups exist (use echopype 0.8.x/0.9.x attribute names)
        assert echodata.top is not None
        assert echodata.environment is not None
        assert echodata.platform is not None
        assert echodata.sonar is not None
        
        # Check for beam group which contains backscatter data
        assert echodata.beam is not None, "Beam data not found"

    def test_convert_multiple_files(self, all_raw_files: list[Path], echopype_available, tmp_path: Path):
        """Convert multiple raw files."""
        from oceanstream.echodata.convert import convert_raw_files
        
        # Only use first 2 files to keep test fast
        files_to_convert = all_raw_files[:2]
        
        results = convert_raw_files(
            files_to_convert,
            tmp_path,
            sonar_model="EK80",
            parallel=False,  # Sequential for predictable output
        )
        
        assert len(results) == len(files_to_convert)
        for output_path in results:
            assert output_path.exists()

    def test_cli_convert_real_file(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Test CLI convert command with real file."""
        from typer.testing import CliRunner
        from oceanstream import cli as cli_module
        
        # Copy just the sample file to a temp dir to avoid converting all
        import shutil
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        shutil.copy(sample_raw_file, input_dir / sample_raw_file.name)
        
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        runner = CliRunner()
        result = runner.invoke(
            cli_module.app,
            [
                "process",
                "echodata",
                "convert",
                "--input-source",
                str(input_dir),
                "--output-dir",
                str(output_dir),
            ],
        )
        
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        
        # Check output was created
        zarr_files = list(output_dir.glob("*.zarr"))
        assert len(zarr_files) >= 1, f"No zarr files created. Output: {result.output}"
