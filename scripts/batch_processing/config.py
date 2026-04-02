"""Configuration for the Saildrone TPOS 2023 batch processing pipeline.

All processing parameters in one place. Can be overridden via CLI flags
or by loading a TOML configuration file for denoise parameters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class DaskConfig:
    """Dask distributed cluster settings."""

    scheduler_address: Optional[str] = None  # None → LocalCluster
    n_workers: int = 4
    threads_per_worker: int = 1
    memory_limit: str = "12GB"


@dataclass
class ChunkConfig:
    """Zarr / xarray chunk sizes used throughout the pipeline."""

    ping_time: int = 1000
    range_sample: int = -1  # -1 means "all" (no chunking along range_sample)

    def as_dict(self) -> dict:
        return {"ping_time": self.ping_time, "range_sample": self.range_sample}


@dataclass
class DenoiseParams:
    """Denoise method toggles and frequency-specific parameters.

    Maps to ``oceanstream.echodata.config.DenoiseConfig`` but is kept
    separate so the processing script can build the config object at
    runtime vs being tied to TOML loading.
    """

    enabled: bool = True
    methods: list[str] = field(
        default_factory=lambda: ["background", "impulse", "transient", "attenuation"]
    )
    # Global defaults (used when use_frequency_specific is False)
    background_num_side_pings: int = 25
    background_snr_threshold: float = 3.0
    impulse_threshold_db: float = 10.0
    impulse_num_lags: int = 3
    transient_n: int = 5
    transient_exclude_above: float = 250.0
    attenuation_threshold: float = 0.8
    attenuation_upper_limit: float = 180.0
    attenuation_lower_limit: float = 280.0
    # Frequency-specific overrides
    use_frequency_specific: bool = False
    frequency_params: Optional[dict] = None

    def to_denoise_config(self):
        """Convert to ``oceanstream.echodata.config.DenoiseConfig``."""
        from oceanstream.echodata.config import DenoiseConfig

        # Normalize frequency_params keys to int (TOML produces str keys)
        freq_params = None
        if self.frequency_params:
            freq_params = {int(k): v for k, v in self.frequency_params.items()}

        return DenoiseConfig(
            methods=self.methods,
            use_frequency_specific=self.use_frequency_specific,
            frequency_params=freq_params,
            background_num_side_pings=self.background_num_side_pings,
            background_snr_threshold=self.background_snr_threshold,
            impulse_threshold_db=self.impulse_threshold_db,
            impulse_num_lags=self.impulse_num_lags,
            transient_n=self.transient_n,
            transient_exclude_above=self.transient_exclude_above,
            attenuation_threshold=self.attenuation_threshold,
            attenuation_upper_limit=self.attenuation_upper_limit,
            attenuation_lower_limit=self.attenuation_lower_limit,
        )


@dataclass
class MVBSParams:
    """MVBS regridding parameters."""

    range_bin: str = "1m"
    ping_time_bin: str = "10s"


@dataclass
class NASCParams:
    """NASC computation parameters."""

    range_bin: str = "10m"
    dist_bin: str = "0.5nmi"


@dataclass
class RawConversionConfig:
    """Settings for converting raw EK80 files to Sv datasets.

    Used by process_from_raw.py — the zarr v3 pipeline that starts
    from raw .raw files instead of pre-computed Sv zarr stores.
    """

    # Azure File Share where raw .raw files are stored
    file_share_name: str = "saildroneraw"
    file_share_path: str = "DATA"

    # Calibration
    calibration_file: str = ""  # path to calibration_values.xlsx

    # Local temp directory for downloaded raw files (deleted after conversion)
    local_raw_dir: Path = field(
        default_factory=lambda: Path("/tmp/oceanstream/raw_downloads")
    )

    # echopype parameters
    sonar_model: str = "EK80"
    waveform_mode: str = "CW"
    encode_mode: str = "complex"

    # Depth
    depth_offset: float = 1.9  # Saildrone transducer depth below waterline (metres)

    # Concurrency knobs
    download_workers: int = 4   # parallel download threads
    convert_workers: int = 1    # parallel conversion workers (memory-bound, keep low)
    download_batch_size: int = 8  # files per download batch


@dataclass
class AzureVMConfig:
    """Azure VM provisioning settings for remote processing."""

    resource_group: str = "ne1-saildrone1-rg"
    location: str = "northeurope"
    vm_size: str = "Standard_E16s_v5"  # 16 vCPU, 128 GB RAM
    vm_name: str = "oceanstream-batch-vm"
    image: str = "Canonical:ubuntu-24_04-lts:server:latest"
    admin_username: str = "oceanstream"
    ssh_key_path: str = "~/.ssh/id_rsa.pub"
    vnet_name: str = "ne1saildronedaskvnet"
    subnet_name: str = "SchedulerSubnet"
    nsg_name: str = "ne1daskschedulervmnsg"
    auto_deallocate: bool = True


@dataclass
class PipelineConfig:
    """Top-level configuration for the batch processing pipeline."""

    # ── Data source ──────────────────────────────────────────────
    cruise_id: str = "SD_TPOS2023_v03"
    source_container: str = "processed"
    output_container: str = ""  # empty → auto-generate
    gps_data_file: Optional[str] = None  # path to exported GPS JSON
    gps_container: str = ""  # Azure blob container for GPS GeoParquet (e.g. "gpsdata")
    gps_blob_path: str = ""  # path within gps_container (default: {cruise_id}/)
    file_list_file: Optional[str] = None  # path to pre-generated file list JSON

    # ── Date range (for testing subsets) ─────────────────────────
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    # ── Processing parameters ────────────────────────────────────
    denoise: DenoiseParams = field(default_factory=DenoiseParams)
    mvbs: MVBSParams = field(default_factory=MVBSParams)
    nasc: NASCParams = field(default_factory=NASCParams)
    chunks: ChunkConfig = field(default_factory=ChunkConfig)
    days_to_combine: int = 1  # 1 = per-day concatenation
    # ── Raw conversion (process_from_raw.py) ─────────────────────
    raw: RawConversionConfig = field(default_factory=RawConversionConfig)
    # ── Processing toggles ───────────────────────────────────────
    surface_exclusion_depth: float = 1.9  # metres — exclude bins above this depth (Saildrone transducer depth)
    apply_seabed_mask: bool = False  # disabled for tropical pacific (no seabed)
    skip_denoising: bool = False
    skip_echograms: bool = False
    skip_pmtiles: bool = False
    skip_nasc: bool = False
    skip_mvbs: bool = False
    save_to_netcdf: bool = False
    save_nasc_to_netcdf: bool = False
    save_mvbs_to_netcdf: bool = False
    per_file_netcdf: bool = False       # export per-file NetCDF in Stage 2
    per_file_echograms: bool = False    # generate per-file echograms in Stage 2
    build_campaign_zarr: bool = True
    build_campaign_sv_zarr: bool = False  # experimental
    category_parallel: bool = True  # parallelize short_pulse/long_pulse within each day
    resume_stage: int = 0               # resume from this stage (0 = start from beginning)\n    keep_raw: bool = False              # keep downloaded raw files after conversion

    # ── Echogram settings ────────────────────────────────────────
    colormap: str = "ocean_r"

    # ── Dask ─────────────────────────────────────────────────────
    dask: DaskConfig = field(default_factory=DaskConfig)
    batch_size: int = 6  # max concurrent Dask futures

    # ── Azure VM (for remote mode) ───────────────────────────────
    azure_vm: AzureVMConfig = field(default_factory=AzureVMConfig)

    # ── Output paths ─────────────────────────────────────────────
    local_output_dir: Path = field(
        default_factory=lambda: Path("/tmp/oceanstream/batch_output")
    )
    local_save_dir: Optional[Path] = None  # None → Azure, set → local filesystem
    upload_after: bool = False  # process locally, bulk upload to Azure at end

    @classmethod
    def from_env(cls) -> PipelineConfig:
        """Build config from environment variables (for VM deployment)."""
        cfg = cls()
        cfg.cruise_id = os.environ.get("CRUISE_ID", cfg.cruise_id)
        cfg.source_container = os.environ.get(
            "PROCESSED_CONTAINER_NAME", cfg.source_container
        )
        cfg.output_container = os.environ.get("OUTPUT_CONTAINER", cfg.output_container)
        cfg.gps_data_file = os.environ.get("GPS_DATA_FILE", cfg.gps_data_file)

        # Dask
        addr = os.environ.get("DASK_CLUSTER_ADDRESS")
        if addr:
            cfg.dask.scheduler_address = addr
        n_workers = os.environ.get("DASK_N_WORKERS")
        if n_workers:
            cfg.dask.n_workers = int(n_workers)
        mem = os.environ.get("DASK_MEMORY_LIMIT")
        if mem:
            cfg.dask.memory_limit = mem

        return cfg

    @classmethod
    def for_local_test(
        cls,
        start_date: str = "2023-05-29",
        end_date: str = "2023-05-31",
        n_workers: int = 4,
    ) -> PipelineConfig:
        """Quick config for local testing with available data.

        Default dates cover May 30, 2023 — the only date currently
        available in the ``processed`` container for v03.
        """
        return cls(
            cruise_id="SD_TPOS2023_v03",
            start_date=datetime.fromisoformat(start_date),
            end_date=datetime.fromisoformat(end_date),
            dask=DaskConfig(n_workers=2, memory_limit="8GB"),
            apply_seabed_mask=False,
            skip_pmtiles=False,
            build_campaign_zarr=True,
            save_to_netcdf=False,
            save_nasc_to_netcdf=True,
            save_mvbs_to_netcdf=True,
            per_file_netcdf=False,
            per_file_echograms=False,
            batch_size=2,
            azure_vm=AzureVMConfig(auto_deallocate=False),
        )
