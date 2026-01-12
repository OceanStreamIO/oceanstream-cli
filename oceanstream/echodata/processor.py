"""High-level Echodata Processor for end-to-end pipeline orchestration.

This module provides the EchodataProcessor class that orchestrates the full
echodata processing pipeline from raw files to final products (MVBS, NASC, echograms).

Pipeline stages:
    1. Convert raw files to EchoData Zarr
    2. Apply calibration (Saildrone Excel or generic)
    3. Enrich with environmental data (CTD or Copernicus fallback)
    4. Concatenate by day (24h batches)
    5. Compute Sv (Volume Backscattering Strength)
    6. Apply denoising (background, transient, impulse, attenuation)
    7. Compute MVBS and NASC
    8. Generate echograms

Designed for both CLI use and library integration with Prefect flows.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import xarray as xr
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result from a single processing step."""
    
    step: str
    success: bool
    output_path: Optional[Path] = None
    message: str = ""
    duration_seconds: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Aggregate result from the full pipeline."""
    
    campaign_id: str
    output_dir: Path
    steps: list[ProcessingResult] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    @property
    def success(self) -> bool:
        """True if all steps succeeded."""
        return all(s.success for s in self.steps)
    
    @property
    def total_duration(self) -> float:
        """Total processing time in seconds."""
        return sum(s.duration_seconds for s in self.steps)
    
    @property
    def failed_steps(self) -> list[ProcessingResult]:
        """List of failed steps."""
        return [s for s in self.steps if not s.success]
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "campaign_id": self.campaign_id,
            "output_dir": str(self.output_dir),
            "success": self.success,
            "total_duration_seconds": self.total_duration,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "steps": [
                {
                    "step": s.step,
                    "success": s.success,
                    "output_path": str(s.output_path) if s.output_path else None,
                    "message": s.message,
                    "duration_seconds": s.duration_seconds,
                    "metadata": s.metadata,
                }
                for s in self.steps
            ],
        }
    
    def save_report(self, path: Path) -> None:
        """Save pipeline report to JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


class EchodataProcessor:
    """High-level processor for echosounder data (EK60/EK80).
    
    Orchestrates the full processing pipeline from raw files to final products.
    Supports both standalone CLI use and integration with Prefect flows.
    
    Example:
        from oceanstream.echodata import EchodataProcessor
        from oceanstream.echodata.config import EchodataConfig
        
        config = EchodataConfig.from_toml(Path("oceanstream.toml"))
        processor = EchodataProcessor(
            config=config,
            campaign_id="TPOS2023",
            verbose=True,
        )
        
        result = processor.run(
            input_dir=Path("./raw_data/saildrone-ek80-raw"),
            output_dir=Path("./output/TPOS2023"),
            geoparquet_dir=Path("./.oceanstream_staging/tpos_2023"),
        )
        
        if result.success:
            print(f"Pipeline completed in {result.total_duration:.1f}s")
    """
    
    def __init__(
        self,
        config: Optional["EchodataConfig"] = None,
        campaign_id: Optional[str] = None,
        verbose: bool = False,
        provider: Optional[Any] = None,  # Legacy compatibility
    ):
        """
        Initialize the processor.
        
        Args:
            config: EchodataConfig with processing parameters (default: EchodataConfig())
            campaign_id: Campaign identifier for output organization
            verbose: Enable detailed progress logging
            provider: Legacy provider argument (deprecated, for backward compatibility)
        """
        # Import here to avoid circular imports
        from oceanstream.echodata.config import EchodataConfig
        
        self.config = config or EchodataConfig()
        self.campaign_id = campaign_id or self.config.campaign_id
        self.verbose = verbose
        self.provider = provider  # Legacy compatibility
        self._start_time = perf_counter()
        
        # Track intermediate results
        self._raw_files: list[Path] = []
        self._converted_paths: list[Path] = []
        self._sv_paths: list[Path] = []
        self._denoised_paths: list[Path] = []
        self._daily_concatenated: dict[str, Path] = {}
    
    def log(self, message: str, level: str = "info") -> None:
        """Log a message if verbose is enabled."""
        if self.verbose:
            log_func = getattr(logger, level, logger.info)
            log_func(f"[EchodataProcessor] {message}")
            print(f"[echodata] {message}")
    
    def elapsed_time(self) -> float:
        """Get elapsed time since processor initialization."""
        return perf_counter() - self._start_time
    
    def _timed_step(self, step_name: str):
        """Context manager for timing a processing step."""
        return _TimedStep(step_name, self)
    
    # =========================================================================
    # Step 1: Convert Raw Files
    # =========================================================================
    
    def convert(
        self,
        input_dir: Path,
        output_dir: Path,
        pattern: str = "*.raw",
    ) -> ProcessingResult:
        """
        Convert raw EK60/EK80 files to EchoData Zarr format.
        
        Args:
            input_dir: Directory containing raw files
            output_dir: Output directory for Zarr stores
            pattern: Glob pattern for raw files
            
        Returns:
            ProcessingResult with conversion status
        """
        from oceanstream.echodata.convert import convert_raw_files
        
        start = perf_counter()
        
        # Discover raw files
        input_dir = Path(input_dir)
        self._raw_files = sorted(input_dir.glob(pattern))
        
        if not self._raw_files:
            return ProcessingResult(
                step="convert",
                success=False,
                message=f"No {pattern} files found in {input_dir}",
                duration_seconds=perf_counter() - start,
            )
        
        self.log(f"Converting {len(self._raw_files)} raw files")
        
        # Setup output
        raw_output = output_dir / "raw"
        raw_output.mkdir(parents=True, exist_ok=True)
        
        try:
            self._converted_paths = convert_raw_files(
                self._raw_files,
                raw_output,
                sonar_model=self.config.sonar_model,
                parallel=self.config.parallel,
                n_workers=self.config.n_workers,
            )
            
            return ProcessingResult(
                step="convert",
                success=True,
                output_path=raw_output,
                message=f"Converted {len(self._converted_paths)} files",
                duration_seconds=perf_counter() - start,
                metadata={"file_count": len(self._converted_paths)},
            )
            
        except Exception as e:
            logger.exception(f"Conversion failed: {e}")
            return ProcessingResult(
                step="convert",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Step 2: Apply Calibration
    # =========================================================================
    
    def calibrate(
        self,
        calibration_file: Optional[Path] = None,
    ) -> ProcessingResult:
        """
        Apply calibration to converted EchoData stores.
        
        Args:
            calibration_file: Path to calibration file (Excel for Saildrone)
                              Uses config.calibration_file if not specified.
                              
        Returns:
            ProcessingResult with calibration status
        """
        from oceanstream.echodata.calibrate import apply_calibration
        from oceanstream.echodata.convert import open_converted
        
        start = perf_counter()
        
        cal_file = calibration_file or self.config.calibration_file
        if not cal_file:
            return ProcessingResult(
                step="calibrate",
                success=True,
                message="No calibration file specified, skipping",
                duration_seconds=perf_counter() - start,
            )
        
        if not Path(cal_file).exists():
            return ProcessingResult(
                step="calibrate",
                success=False,
                message=f"Calibration file not found: {cal_file}",
                duration_seconds=perf_counter() - start,
            )
        
        if not self._converted_paths:
            return ProcessingResult(
                step="calibrate",
                success=False,
                message="No converted files to calibrate. Run convert() first.",
                duration_seconds=perf_counter() - start,
            )
        
        self.log(f"Applying calibration from {cal_file}")
        
        try:
            calibrated_count = 0
            for zarr_path in self._converted_paths:
                ed = open_converted(zarr_path)
                ed = apply_calibration(ed, cal_file)
                ed.to_zarr(zarr_path, overwrite=True)
                calibrated_count += 1
            
            return ProcessingResult(
                step="calibrate",
                success=True,
                message=f"Calibrated {calibrated_count} files",
                duration_seconds=perf_counter() - start,
                metadata={"calibration_file": str(cal_file)},
            )
            
        except Exception as e:
            logger.exception(f"Calibration failed: {e}")
            return ProcessingResult(
                step="calibrate",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Step 3: Enrich with Environmental Data
    # =========================================================================
    
    def enrich_environment(
        self,
        geoparquet_dir: Optional[Path] = None,
        use_copernicus_fallback: Optional[bool] = None,
    ) -> ProcessingResult:
        """
        Enrich EchoData with environmental parameters (T, S, sound speed, absorption).
        
        Uses in-situ CTD from geoparquet when available, falling back to
        Copernicus Marine Service model data if configured.
        
        Args:
            geoparquet_dir: Path to campaign geoparquet with CTD data
            use_copernicus_fallback: Whether to use Copernicus when CTD unavailable
            
        Returns:
            ProcessingResult with enrichment status
        """
        from oceanstream.echodata.environment import enrich_environment as enrich_env
        
        start = perf_counter()
        
        use_fallback = (
            use_copernicus_fallback 
            if use_copernicus_fallback is not None 
            else self.config.use_copernicus_fallback
        )
        
        if not geoparquet_dir:
            if not use_fallback:
                return ProcessingResult(
                    step="enrich_environment",
                    success=True,
                    message="No geoparquet_dir and Copernicus fallback disabled, skipping",
                    duration_seconds=perf_counter() - start,
                )
            self.log("No geoparquet_dir, will use Copernicus fallback if needed")
        
        if not self._converted_paths:
            return ProcessingResult(
                step="enrich_environment",
                success=False,
                message="No converted files to enrich. Run convert() first.",
                duration_seconds=perf_counter() - start,
            )
        
        self.log(f"Enriching {len(self._converted_paths)} files with environmental data")
        
        try:
            enriched_count = 0
            for zarr_path in self._converted_paths:
                enrich_env(
                    zarr_path,
                    geoparquet_dir,
                    use_copernicus_fallback=use_fallback,
                )
                enriched_count += 1
            
            return ProcessingResult(
                step="enrich_environment",
                success=True,
                message=f"Enriched {enriched_count} files",
                duration_seconds=perf_counter() - start,
                metadata={
                    "geoparquet_dir": str(geoparquet_dir) if geoparquet_dir else None,
                    "copernicus_fallback": use_fallback,
                },
            )
            
        except Exception as e:
            logger.exception(f"Environment enrichment failed: {e}")
            return ProcessingResult(
                step="enrich_environment",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Step 4: Daily Concatenation
    # =========================================================================
    
    def concatenate_daily(
        self,
        output_dir: Path,
    ) -> ProcessingResult:
        """
        Group and concatenate EchoData by day (24h batches).
        
        Args:
            output_dir: Output directory for daily concatenated Zarr stores
            
        Returns:
            ProcessingResult with concatenation status
        """
        from oceanstream.echodata.concat import (
            group_files_by_day,
            concatenate_daily as concat_daily,
        )
        
        start = perf_counter()
        
        if not self._raw_files:
            return ProcessingResult(
                step="concatenate_daily",
                success=False,
                message="No raw files tracked. Run convert() first.",
                duration_seconds=perf_counter() - start,
            )
        
        # Group raw files by date
        day_groups = group_files_by_day(self._raw_files)
        self.log(f"Found {len(day_groups)} day groups to concatenate")
        
        # Build mapping from raw file -> converted path
        raw_to_zarr = {}
        for raw_file in self._raw_files:
            for zarr_path in self._converted_paths:
                if zarr_path.stem == raw_file.stem:
                    raw_to_zarr[raw_file] = zarr_path
                    break
        
        daily_output = output_dir / "daily"
        daily_output.mkdir(parents=True, exist_ok=True)
        
        try:
            self._daily_concatenated = {}
            for date_str, files in day_groups.items():
                zarr_paths = [raw_to_zarr[f] for f in files if f in raw_to_zarr]
                if not zarr_paths:
                    self.log(f"No converted files for date {date_str}, skipping", "warning")
                    continue
                
                self.log(f"Concatenating {len(zarr_paths)} files for {date_str}")
                output_path = daily_output / f"{date_str}.zarr"
                
                concat_daily(zarr_paths, output_path)
                self._daily_concatenated[date_str] = output_path
            
            return ProcessingResult(
                step="concatenate_daily",
                success=True,
                output_path=daily_output,
                message=f"Created {len(self._daily_concatenated)} daily datasets",
                duration_seconds=perf_counter() - start,
                metadata={"dates": list(self._daily_concatenated.keys())},
            )
            
        except Exception as e:
            logger.exception(f"Daily concatenation failed: {e}")
            return ProcessingResult(
                step="concatenate_daily",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Step 5: Compute Sv
    # =========================================================================
    
    def compute_sv(
        self,
        output_dir: Path,
        input_paths: Optional[list[Path]] = None,
        env_params: Optional[dict] = None,
    ) -> ProcessingResult:
        """
        Compute Sv (Volume Backscattering Strength) from EchoData.
        
        Args:
            output_dir: Output directory for Sv Zarr stores
            input_paths: Specific paths to process (default: daily concatenated or converted)
            env_params: Optional environment parameter overrides
            
        Returns:
            ProcessingResult with Sv computation status
        """
        from oceanstream.echodata.compute import compute_sv as sv_compute
        
        start = perf_counter()
        
        # Determine input paths
        if input_paths:
            paths_to_process = input_paths
        elif self._daily_concatenated:
            paths_to_process = list(self._daily_concatenated.values())
        elif self._converted_paths:
            paths_to_process = self._converted_paths
        else:
            return ProcessingResult(
                step="compute_sv",
                success=False,
                message="No input paths available. Run convert() or concatenate_daily() first.",
                duration_seconds=perf_counter() - start,
            )
        
        sv_output = output_dir / "sv"
        sv_output.mkdir(parents=True, exist_ok=True)
        
        self.log(f"Computing Sv for {len(paths_to_process)} datasets")
        
        try:
            self._sv_paths = []
            for echodata_path in paths_to_process:
                output_path = sv_output / f"{echodata_path.stem}_Sv.zarr"
                
                sv_compute(
                    echodata_path,
                    output_path=output_path,
                    env_params=env_params,
                    add_depth=True,
                    add_location=True,
                )
                self._sv_paths.append(output_path)
            
            return ProcessingResult(
                step="compute_sv",
                success=True,
                output_path=sv_output,
                message=f"Computed Sv for {len(self._sv_paths)} datasets",
                duration_seconds=perf_counter() - start,
                metadata={"sv_count": len(self._sv_paths)},
            )
            
        except Exception as e:
            logger.exception(f"Sv computation failed: {e}")
            return ProcessingResult(
                step="compute_sv",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Step 6: Apply Denoising
    # =========================================================================
    
    def denoise(
        self,
        output_dir: Path,
        input_paths: Optional[list[Path]] = None,
        methods: Optional[list[str]] = None,
    ) -> ProcessingResult:
        """
        Apply denoising pipeline to Sv datasets.
        
        Args:
            output_dir: Output directory for denoised Zarr stores
            input_paths: Specific Sv paths to denoise (default: computed Sv paths)
            methods: Denoising methods to apply (default: from config)
            
        Returns:
            ProcessingResult with denoising status
        """
        from oceanstream.echodata.denoise import apply_denoising
        
        start = perf_counter()
        
        # Determine input paths
        if input_paths:
            paths_to_process = input_paths
        elif self._sv_paths:
            paths_to_process = self._sv_paths
        else:
            return ProcessingResult(
                step="denoise",
                success=False,
                message="No Sv paths available. Run compute_sv() first.",
                duration_seconds=perf_counter() - start,
            )
        
        denoise_methods = methods or self.config.denoise.methods
        
        denoised_output = output_dir / "denoised"
        denoised_output.mkdir(parents=True, exist_ok=True)
        
        self.log(f"Applying denoising ({denoise_methods}) to {len(paths_to_process)} datasets")
        
        try:
            self._denoised_paths = []
            for sv_path in paths_to_process:
                output_path = denoised_output / f"{sv_path.stem}_denoised.zarr"
                
                apply_denoising(
                    sv_path,
                    methods=denoise_methods,
                    config=self.config.denoise,
                    output_path=output_path,
                )
                self._denoised_paths.append(output_path)
            
            return ProcessingResult(
                step="denoise",
                success=True,
                output_path=denoised_output,
                message=f"Denoised {len(self._denoised_paths)} datasets",
                duration_seconds=perf_counter() - start,
                metadata={"methods": denoise_methods},
            )
            
        except Exception as e:
            logger.exception(f"Denoising failed: {e}")
            return ProcessingResult(
                step="denoise",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Step 7: Compute MVBS and NASC
    # =========================================================================
    
    def compute_mvbs(
        self,
        output_dir: Path,
        input_paths: Optional[list[Path]] = None,
        range_bin: Optional[str] = None,
        ping_time_bin: Optional[str] = None,
    ) -> ProcessingResult:
        """
        Compute MVBS (Mean Volume Backscattering Strength).
        
        Args:
            output_dir: Output directory for MVBS Zarr stores
            input_paths: Sv paths to process (default: denoised or Sv paths)
            range_bin: Vertical bin size (default: from config)
            ping_time_bin: Temporal bin size (default: from config)
            
        Returns:
            ProcessingResult with MVBS computation status
        """
        from oceanstream.echodata.compute import compute_mvbs as mvbs_compute
        import xarray as xr
        
        start = perf_counter()
        
        # Determine input paths
        if input_paths:
            paths_to_process = input_paths
        elif self._denoised_paths:
            paths_to_process = self._denoised_paths
        elif self._sv_paths:
            paths_to_process = self._sv_paths
        else:
            return ProcessingResult(
                step="compute_mvbs",
                success=False,
                message="No Sv paths available. Run compute_sv() or denoise() first.",
                duration_seconds=perf_counter() - start,
            )
        
        r_bin = range_bin or self.config.mvbs.range_bin
        pt_bin = ping_time_bin or self.config.mvbs.ping_time_bin
        
        mvbs_output = output_dir / "mvbs"
        mvbs_output.mkdir(parents=True, exist_ok=True)
        
        self.log(f"Computing MVBS (range_bin={r_bin}, ping_time_bin={pt_bin})")
        
        try:
            mvbs_paths = []
            for sv_path in paths_to_process:
                sv_ds = xr.open_zarr(sv_path)
                output_path = mvbs_output / f"{sv_path.stem}_mvbs.zarr"
                
                mvbs_compute(
                    sv_ds,
                    range_bin=r_bin,
                    ping_time_bin=pt_bin,
                    output_path=output_path,
                )
                mvbs_paths.append(output_path)
            
            return ProcessingResult(
                step="compute_mvbs",
                success=True,
                output_path=mvbs_output,
                message=f"Computed MVBS for {len(mvbs_paths)} datasets",
                duration_seconds=perf_counter() - start,
                metadata={
                    "range_bin": r_bin,
                    "ping_time_bin": pt_bin,
                    "mvbs_count": len(mvbs_paths),
                },
            )
            
        except Exception as e:
            logger.exception(f"MVBS computation failed: {e}")
            return ProcessingResult(
                step="compute_mvbs",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    def compute_nasc(
        self,
        output_dir: Path,
        input_paths: Optional[list[Path]] = None,
        range_bin: Optional[str] = None,
        dist_bin: Optional[str] = None,
    ) -> ProcessingResult:
        """
        Compute NASC (Nautical Area Scattering Coefficient).
        
        Args:
            output_dir: Output directory for NASC Zarr stores
            input_paths: Sv paths to process (default: denoised or Sv paths)
            range_bin: Vertical bin size (default: from config)
            dist_bin: Distance bin size (default: from config)
            
        Returns:
            ProcessingResult with NASC computation status
        """
        from oceanstream.echodata.compute import compute_nasc as nasc_compute
        import xarray as xr
        
        start = perf_counter()
        
        # Determine input paths
        if input_paths:
            paths_to_process = input_paths
        elif self._denoised_paths:
            paths_to_process = self._denoised_paths
        elif self._sv_paths:
            paths_to_process = self._sv_paths
        else:
            return ProcessingResult(
                step="compute_nasc",
                success=False,
                message="No Sv paths available. Run compute_sv() or denoise() first.",
                duration_seconds=perf_counter() - start,
            )
        
        r_bin = range_bin or self.config.nasc.range_bin
        d_bin = dist_bin or self.config.nasc.dist_bin
        
        nasc_output = output_dir / "nasc"
        nasc_output.mkdir(parents=True, exist_ok=True)
        
        self.log(f"Computing NASC (range_bin={r_bin}, dist_bin={d_bin})")
        
        try:
            nasc_paths = []
            for sv_path in paths_to_process:
                sv_ds = xr.open_zarr(sv_path)
                output_path = nasc_output / f"{sv_path.stem}_nasc.zarr"
                
                nasc_compute(
                    sv_ds,
                    range_bin=r_bin,
                    dist_bin=d_bin,
                    output_path=output_path,
                )
                nasc_paths.append(output_path)
            
            return ProcessingResult(
                step="compute_nasc",
                success=True,
                output_path=nasc_output,
                message=f"Computed NASC for {len(nasc_paths)} datasets",
                duration_seconds=perf_counter() - start,
                metadata={
                    "range_bin": r_bin,
                    "dist_bin": d_bin,
                    "nasc_count": len(nasc_paths),
                },
            )
            
        except Exception as e:
            logger.exception(f"NASC computation failed: {e}")
            return ProcessingResult(
                step="compute_nasc",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Step 8: Generate Echograms
    # =========================================================================
    
    def generate_echograms(
        self,
        output_dir: Path,
        input_paths: Optional[list[Path]] = None,
        cmap: str = "ocean_r",
        vmin: float = -80.0,
        vmax: float = -50.0,
        dpi: int = 180,
    ) -> ProcessingResult:
        """
        Generate echogram visualizations from Sv datasets.
        
        Args:
            output_dir: Output directory for echogram images
            input_paths: Sv paths to plot (default: denoised or Sv paths)
            cmap: Matplotlib colormap name
            vmin: Minimum Sv value for color scale (dB)
            vmax: Maximum Sv value for color scale (dB)
            dpi: Output image resolution
            
        Returns:
            ProcessingResult with echogram generation status
        """
        from oceanstream.echodata.plot import generate_echograms as gen_echograms
        import xarray as xr
        
        start = perf_counter()
        
        # Determine input paths
        if input_paths:
            paths_to_process = input_paths
        elif self._denoised_paths:
            paths_to_process = self._denoised_paths
        elif self._sv_paths:
            paths_to_process = self._sv_paths
        else:
            return ProcessingResult(
                step="generate_echograms",
                success=False,
                message="No Sv paths available. Run compute_sv() or denoise() first.",
                duration_seconds=perf_counter() - start,
            )
        
        echogram_output = output_dir / "echograms"
        echogram_output.mkdir(parents=True, exist_ok=True)
        
        self.log(f"Generating echograms for {len(paths_to_process)} datasets")
        
        try:
            all_echograms = []
            for sv_path in paths_to_process:
                sv_ds = xr.open_zarr(sv_path)
                base_name = sv_path.stem.replace("_Sv", "").replace("_denoised", "")
                
                echogram_files = gen_echograms(
                    sv_ds,
                    output_dir=echogram_output,
                    file_base_name=base_name,
                    cmap=cmap,
                    vmin=vmin,
                    vmax=vmax,
                    dpi=dpi,
                )
                all_echograms.extend(echogram_files)
            
            return ProcessingResult(
                step="generate_echograms",
                success=True,
                output_path=echogram_output,
                message=f"Generated {len(all_echograms)} echograms",
                duration_seconds=perf_counter() - start,
                metadata={
                    "echogram_count": len(all_echograms),
                    "files": [str(f) for f in all_echograms],
                },
            )
            
        except Exception as e:
            logger.exception(f"Echogram generation failed: {e}")
            return ProcessingResult(
                step="generate_echograms",
                success=False,
                message=str(e),
                duration_seconds=perf_counter() - start,
            )
    
    # =========================================================================
    # Full Pipeline Execution
    # =========================================================================
    
    def run(
        self,
        input_dir: Path,
        output_dir: Path,
        geoparquet_dir: Optional[Path] = None,
        calibration_file: Optional[Path] = None,
        skip_concatenation: bool = False,
        skip_denoising: bool = False,
        skip_mvbs: bool = False,
        skip_nasc: bool = False,
        skip_echograms: bool = False,
    ) -> PipelineResult:
        """
        Run the full echodata processing pipeline.
        
        Pipeline stages:
            1. Convert raw files to EchoData Zarr
            2. Apply calibration (if file provided)
            3. Enrich with environmental data (if geoparquet provided)
            4. Concatenate by day (unless skipped)
            5. Compute Sv
            6. Apply denoising (unless skipped)
            7. Compute MVBS and NASC (unless skipped)
            8. Generate echograms (unless skipped)
        
        Args:
            input_dir: Directory containing raw EK60/EK80 files
            output_dir: Base output directory for all products
            geoparquet_dir: Path to campaign geoparquet with CTD data
            calibration_file: Path to calibration file
            skip_concatenation: Skip daily concatenation step
            skip_denoising: Skip denoising step
            skip_mvbs: Skip MVBS computation
            skip_nasc: Skip NASC computation
            skip_echograms: Skip echogram generation
            
        Returns:
            PipelineResult with status and output paths
        """
        result = PipelineResult(
            campaign_id=self.campaign_id or "unknown",
            output_dir=Path(output_dir),
            start_time=datetime.now(),
        )
        
        output_dir = Path(output_dir)
        if self.campaign_id:
            output_dir = output_dir / self.campaign_id / "echodata"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        self.log(f"Starting pipeline for campaign '{result.campaign_id}'")
        self.log(f"Output directory: {output_dir}")
        
        # Step 1: Convert
        step_result = self.convert(input_dir, output_dir)
        result.steps.append(step_result)
        if not step_result.success:
            self.log(f"Convert failed: {step_result.message}", "error")
            result.end_time = datetime.now()
            return result
        
        # Step 2: Calibrate
        step_result = self.calibrate(calibration_file)
        result.steps.append(step_result)
        # Calibration failure is non-fatal
        
        # Step 3: Enrich environment
        step_result = self.enrich_environment(geoparquet_dir)
        result.steps.append(step_result)
        # Environment enrichment failure is non-fatal
        
        # Step 4: Daily concatenation
        if not skip_concatenation and len(self._raw_files) > 1:
            step_result = self.concatenate_daily(output_dir)
            result.steps.append(step_result)
            # Non-fatal
        
        # Step 5: Compute Sv
        step_result = self.compute_sv(output_dir)
        result.steps.append(step_result)
        if not step_result.success:
            self.log(f"Sv computation failed: {step_result.message}", "error")
            result.end_time = datetime.now()
            return result
        
        # Step 6: Denoise
        if not skip_denoising:
            step_result = self.denoise(output_dir)
            result.steps.append(step_result)
            # Non-fatal
        
        # Step 7a: MVBS
        if not skip_mvbs:
            step_result = self.compute_mvbs(output_dir)
            result.steps.append(step_result)
        
        # Step 7b: NASC
        if not skip_nasc:
            step_result = self.compute_nasc(output_dir)
            result.steps.append(step_result)
        
        # Step 8: Echograms
        if not skip_echograms:
            step_result = self.generate_echograms(output_dir)
            result.steps.append(step_result)
        
        result.end_time = datetime.now()
        
        # Summary
        if result.success:
            self.log(f"Pipeline completed successfully in {result.total_duration:.1f}s")
        else:
            failed = [s.step for s in result.failed_steps]
            self.log(f"Pipeline completed with failures in: {failed}", "warning")
        
        # Save report
        report_path = output_dir / "pipeline_report.json"
        result.save_report(report_path)
        self.log(f"Report saved to {report_path}")
        
        return result


class _TimedStep:
    """Context manager for timing processing steps."""
    
    def __init__(self, name: str, processor: EchodataProcessor):
        self.name = name
        self.processor = processor
        self.start = 0.0
    
    def __enter__(self):
        self.start = perf_counter()
        self.processor.log(f"Starting {self.name}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = perf_counter() - self.start
        if exc_type is None:
            self.processor.log(f"Completed {self.name} in {duration:.1f}s")
        else:
            self.processor.log(f"Failed {self.name} after {duration:.1f}s: {exc_val}", "error")


# Legacy function for backward compatibility
def process(
    provider: Any,
    input_dir: Path,
    output_dir: Path,
    verbose: bool = False,
    dry_run: bool = False,
) -> None:
    """
    Legacy process function for backward compatibility with CLI.
    
    Args:
        provider: Data provider instance (used for provider.name)
        input_dir: Directory containing input echosounder files
        output_dir: Output directory for Zarr datasets
        verbose: Enable detailed progress information
        dry_run: Analyze inputs without writing files
    """
    from oceanstream.echodata.config import EchodataConfig
    
    if dry_run:
        print("[echodata] Dry Run Summary")
        print("─" * 40)
        print(f"Source directory : {input_dir}")
        print(f"Output (planned) : {output_dir} (Zarr)")
        print(f"Provider         : {provider.name if hasattr(provider, 'name') else 'unknown'}")
        print("Instrument       : EK60/EK80 (auto-detect)")
        print("Actions          : Would run full pipeline:")
        print("  1. Convert raw files to EchoData Zarr")
        print("  2. Apply calibration (if available)")
        print("  3. Enrich with environmental data")
        print("  4. Concatenate by day")
        print("  5. Compute Sv")
        print("  6. Apply denoising")
        print("  7. Compute MVBS and NASC")
        print("  8. Generate echograms")
        print("Dependencies     : echopype, zarr, xarray")
        return
    
    config = EchodataConfig()
    processor = EchodataProcessor(config=config, verbose=verbose)
    
    result = processor.run(
        input_dir=Path(input_dir),
        output_dir=Path(output_dir),
    )
    
    if not result.success:
        failed_steps = ", ".join(s.step for s in result.failed_steps)
        print(f"[echodata] Pipeline failed at: {failed_steps}")
    else:
        print(f"[echodata] ✓ Pipeline completed in {result.total_duration:.1f}s")

