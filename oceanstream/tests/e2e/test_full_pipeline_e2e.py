"""E2E tests for full echodata processing pipeline."""

from pathlib import Path
import pytest
import numpy as np


@pytest.mark.skip(reason="Full pipeline tests deferred - depends on denoise module")
@pytest.mark.e2e
class TestFullPipelineE2E:
    """End-to-end tests for complete echodata processing pipeline."""

    def test_raw_to_mvbs_pipeline(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Test complete pipeline: Raw → Zarr → Sv → Denoise → MVBS."""
        import echopype as ep
        from oceanstream.echodata.compute.sv import compute_sv_from_echodata
        from oceanstream.echodata.denoise import apply_denoising
        from oceanstream.echodata.compute.mvbs import compute_mvbs
        from oceanstream.echodata.config import DenoiseConfig, MVBSConfig
        
        # Step 1: Convert raw to EchoData
        print(f"Loading {sample_raw_file.name}...")
        echodata = ep.open_raw(sample_raw_file, sonar_model="EK80")
        assert echodata is not None
        
        # Step 2: Compute Sv
        print("Computing Sv...")
        ds_Sv = compute_sv_from_echodata(echodata, add_depth=True, add_location=True)
        assert "Sv" in ds_Sv.data_vars
        print(f"  Sv shape: {ds_Sv['Sv'].shape}")
        
        # Step 3: Denoise
        print("Applying denoising...")
        denoise_config = DenoiseConfig(
            background_num_side_pings=25,
            transient_a=2.0,
            impulse_threshold_db=10.0,
        )
        ds_denoised = apply_denoising(
            ds_Sv, denoise_config, methods=["background", "transient"]
        )
        assert "Sv" in ds_denoised.data_vars
        
        # Step 4: Compute MVBS
        print("Computing MVBS...")
        mvbs_config = MVBSConfig(range_bin_m=10.0, ping_time_bin_s=60)
        ds_mvbs = compute_mvbs(ds_denoised, mvbs_config)
        assert "Sv" in ds_mvbs.data_vars
        print(f"  MVBS shape: {ds_mvbs['Sv'].shape}")
        
        # Step 5: Save to zarr
        output_path = tmp_path / "pipeline_output.zarr"
        ds_mvbs.to_zarr(output_path, mode="w")
        assert output_path.exists()
        
        print(f"Pipeline complete! Output: {output_path}")

    def test_raw_to_nasc_pipeline(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Test complete pipeline: Raw → Sv → NASC."""
        import echopype as ep
        from oceanstream.echodata.compute.sv import compute_sv_from_echodata
        from oceanstream.echodata.compute.nasc import compute_nasc
        from oceanstream.echodata.config import NASCConfig
        
        # Load and compute Sv
        echodata = ep.open_raw(sample_raw_file, sonar_model="EK80")
        ds_Sv = compute_sv_from_echodata(echodata, add_depth=True, add_location=True)
        
        # Compute NASC
        nasc_config = NASCConfig(range_bin_m=10.0, dist_bin_nmi=0.5)
        ds_nasc = compute_nasc(ds_Sv, nasc_config)
        
        assert "NASC" in ds_nasc.data_vars
        
        # Save output
        output_path = tmp_path / "nasc_output.zarr"
        ds_nasc.to_zarr(output_path, mode="w")
        assert output_path.exists()

    def test_multiple_files_concatenation(self, all_raw_files: list[Path], echopype_available, tmp_path: Path):
        """Test processing and concatenating multiple raw files."""
        import echopype as ep
        import xarray as xr
        from oceanstream.echodata.compute.sv import compute_sv_from_echodata
        
        # Use first 2 files to keep test reasonable
        files = all_raw_files[:2]
        
        sv_datasets = []
        for raw_file in files:
            print(f"Processing {raw_file.name}...")
            echodata = ep.open_raw(raw_file, sonar_model="EK80")
            ds_Sv = compute_sv_from_echodata(echodata, add_depth=False, add_location=False)
            sv_datasets.append(ds_Sv)
        
        # Concatenate along ping_time
        ds_combined = xr.concat(sv_datasets, dim="ping_time")
        
        assert ds_combined.dims["ping_time"] > sv_datasets[0].dims["ping_time"]
        print(f"Combined dataset: {ds_combined.dims['ping_time']} pings")

    def test_pipeline_with_enrichment(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Test pipeline with full Sv enrichment."""
        import echopype as ep
        from oceanstream.echodata.compute.sv import compute_sv_from_echodata, enrich_sv_dataset
        
        echodata = ep.open_raw(sample_raw_file, sonar_model="EK80")
        
        # Compute basic Sv without enrichment
        ds_Sv = ep.calibrate.compute_Sv(echodata)
        
        # Apply enrichment
        ds_enriched = enrich_sv_dataset(
            ds_Sv,
            echodata,
            add_depth=True,
            add_location=True,
            depth_offset=1.9,  # Saildrone transducer depth
        )
        
        assert ds_enriched is not None
        # Should have location if GPS was available
        if "latitude" in ds_enriched.coords:
            lat = ds_enriched["latitude"].values
            valid_lat = lat[~np.isnan(lat)]
            if len(valid_lat) > 0:
                assert -90 <= valid_lat.mean() <= 90


@pytest.mark.skip(reason="CLI pipeline tests deferred - depends on full echodata CLI implementation")
@pytest.mark.e2e
class TestCLIFullPipelineE2E:
    """E2E tests for CLI commands with real data."""

    def test_cli_convert_and_compute_sv(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Test CLI convert followed by compute-sv."""
        from typer.testing import CliRunner
        from oceanstream import cli as cli_module
        
        runner = CliRunner()
        
        # First convert
        convert_dir = tmp_path / "converted"
        convert_dir.mkdir()
        
        result = runner.invoke(
            cli_module.app,
            [
                "process", "echodata", "convert",
                "--input-source", str(sample_raw_file.parent),
                "--output-dir", str(convert_dir),
                "--file-pattern", sample_raw_file.name,
            ],
        )
        assert result.exit_code == 0, f"Convert failed: {result.output}"
        
        # Find converted zarr
        zarr_files = list(convert_dir.glob("*.zarr"))
        assert len(zarr_files) >= 1, f"No zarr created: {result.output}"
        
        # Then compute Sv
        sv_dir = tmp_path / "sv"
        sv_dir.mkdir()
        
        result = runner.invoke(
            cli_module.app,
            [
                "process", "echodata", "compute-sv",
                "--input-source", str(convert_dir),
                "--output-dir", str(sv_dir),
            ],
        )
        assert result.exit_code == 0, f"Compute-sv failed: {result.output}"

    def test_cli_full_pipeline_dry_run(self, sample_raw_file: Path, echopype_available, tmp_path: Path):
        """Test CLI pipeline dry-run shows all steps."""
        from typer.testing import CliRunner
        from oceanstream import cli as cli_module
        
        runner = CliRunner()
        
        result = runner.invoke(
            cli_module.app,
            [
                "process", "echodata", "convert",
                "--input-source", str(sample_raw_file.parent),
                "--output-dir", str(tmp_path),
                "--dry-run",
            ],
        )
        
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()
