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
    """Denoise method toggles and per-stage parameters.

    Global defaults follow Ryan et al. (2015), "Reducing bias due to noise and
    attenuation in open-ocean echo integration data" (ICES J. Mar. Sci. 72(8)),
    tuned to 38 kHz. Values match the ``[echodata.denoise.frequency_params.38000]``
    section of ``ryan2015_denoise_defaults.toml``, so running the shell script
    without ``--denoise-config`` gives equivalent behaviour to loading that TOML
    in global mode.

    Assumes a Saildrone-class deployment reaching ≥600 m at 38 kHz. Higher
    frequencies (120/200/333 kHz) that cannot see the 400–500 m attenuation
    reference band get an empty AS mask (safe fallback in
    :func:`attenuation_mask`). For proper per-frequency tuning use
    ``--denoise-config ryan2015_denoise_defaults.toml`` which flips
    ``use_frequency_specific=True``.

    Maps to :class:`oceanstream.echodata.config.DenoiseConfig`.
    """

    enabled: bool = True

    # Processing order per Ryan et al. (2015) Figure 2:
    #   impulse → attenuation → transient → background
    # In :func:`denoise_day` the three mask methods (impulse/attenuation/
    # transient) are ORed together in one pass; ``background`` is applied
    # last as an echopype Sv-domain subtraction. Order in this list only
    # selects which filters run.
    methods: list[str] = field(
        default_factory=lambda: ["impulse", "attenuation", "transient", "background"]
    )

    # Frequency-specific overrides (optional — flips per-channel dispatch on)
    use_frequency_specific: bool = False
    frequency_params: Optional[dict] = None

    # ── Background noise — De Robertis & Higginbottom (2007) ────────────
    # Ryan 2015 @ 38 kHz: 30-sample × 50-ping block, SNR ≥ 5 dB,
    # background_noise_max = −125 dB (typical 38 kHz noise floor cap).
    # Without background_noise_max the estimated noise can spike in quiet
    # blocks and smear/lift the near-surface Sv floor after subtraction.
    background_range_window: int = 30
    background_ping_window: int = 50
    background_snr_threshold: float = 5.0            # dB
    background_noise_max: Optional[float] = -125.0   # dB, cap on estimated noise
    background_num_side_pings: int = 25              # unused by echopype path; kept for API compat

    # ── Impulse noise — multi-lag comparison ────────────────────────────
    # Ryan 2015: 5 m vertical bin, ping_lags = [1, 2], threshold = 10–12 dB.
    # Two lags are recommended over one for robustness against paired spikes.
    impulse_threshold_db: float = 12.0
    impulse_vertical_bin: float = 5.0                # metres
    impulse_ping_lags: list[int] = field(default_factory=lambda: [1, 2])
    impulse_num_lags: int = 2                        # informational; ping_lags drives behaviour

    # ── Transient noise — Fielding upward-stepping filter ───────────────
    # Ryan 2015 @ 38 kHz: exclude_above = 200 m, depth_bin = 20 m,
    # n_pings = 50, thr_dB = 15. Aggressive thresholds (< 12 dB) tend to
    # smear surface reverberation downward, so keep this ≥ 12.
    transient_exclude_above: float = 200.0           # metres
    transient_depth_bin: float = 20.0                # metres
    transient_n_pings: int = 50
    transient_threshold_db: float = 15.0             # dB
    transient_n: int = 5                             # legacy field, unused by Fielding filter

    # ── Attenuated signal (AS) — full-ping rejection ────────────────────
    # Ryan 2015 @ 38 kHz: reference layer 400–500 m, 100 side pings,
    # threshold 8–10 dB below block median flags the whole ping.
    # NOTE: earlier releases shipped 0.8 dB here which flagged ~8% of
    # pings on normal DSL variability — do NOT lower below ~6 dB.
    attenuation_threshold: float = 10.0              # dB
    attenuation_upper_limit: float = 400.0           # metres, top of reference band
    attenuation_lower_limit: float = 500.0           # metres, bottom of reference band
    attenuation_side_pings: int = 100

    # ── Post-denoise sanity clip ────────────────────────────────────────
    # Anything > sv_clip_max_db in the water column is not biology — it's
    # residual cross-talk, electrical interference, or ring-down that the
    # mask-based denoisers missed. Set to None to disable.
    sv_clip_max_db: Optional[float] = -10.0

    def to_denoise_config(self):
        """Convert to :class:`oceanstream.echodata.config.DenoiseConfig`."""
        from oceanstream.echodata.config import DenoiseConfig

        # Normalize frequency_params keys to int (TOML produces str keys)
        freq_params = None
        if self.frequency_params:
            freq_params = {int(k): v for k, v in self.frequency_params.items()}

        return DenoiseConfig(
            methods=self.methods,
            use_frequency_specific=self.use_frequency_specific,
            frequency_params=freq_params,
            # Background
            background_num_side_pings=self.background_num_side_pings,
            background_range_window=self.background_range_window,
            background_ping_window=self.background_ping_window,
            background_snr_threshold=self.background_snr_threshold,
            background_noise_max=self.background_noise_max,
            # Impulse
            impulse_threshold_db=self.impulse_threshold_db,
            impulse_num_lags=self.impulse_num_lags,
            impulse_vertical_bin=self.impulse_vertical_bin,
            impulse_ping_lags=list(self.impulse_ping_lags),
            # Transient
            transient_n=self.transient_n,
            transient_exclude_above=self.transient_exclude_above,
            transient_depth_bin=self.transient_depth_bin,
            transient_n_pings=self.transient_n_pings,
            transient_threshold_db=self.transient_threshold_db,
            # Attenuation
            attenuation_threshold=self.attenuation_threshold,
            attenuation_upper_limit=self.attenuation_upper_limit,
            attenuation_lower_limit=self.attenuation_lower_limit,
            attenuation_side_pings=self.attenuation_side_pings,
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
class PruneParams:
    """Drop pings that are mostly NaN after denoising.

    Runs between seabed masking and MVBS/NASC. Pings whose NaN fraction
    exceeds ``drop_threshold`` are removed from the ping_time axis so that
    downstream linear-space averaging (MVBS) isn't dominated by the residual
    non-NaN samples of otherwise garbage pings.
    """

    enabled: bool = True
    drop_threshold: float = 0.8  # drop pings with >= 80% NaN samples
    # Cross-talk detection — flag pings where a "quiet" deep reference band has
    # abnormally elevated Sv (a signature of interference from another
    # echosounder, ADCP, or electrical source). The mesopelagic below the DSL
    # at 38 kHz in tropical open ocean is near the noise floor; systematic
    # elevation there means the ping is contaminated across the whole column.
    crosstalk_enabled: bool = True
    crosstalk_ref_depth_min: float = 800.0    # metres — start of reference band
    crosstalk_ref_depth_max: float = 1200.0   # metres — end of reference band
    crosstalk_threshold_db: float = 6.0       # elevation above median to flag


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
    prune: PruneParams = field(default_factory=PruneParams)
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
    skip_campaign_echograms: bool = False  # skip only the campaign echogram loop (keep campaign zarr)
    build_campaign_sv_zarr: bool = False  # experimental
    category_parallel: bool = True  # parallelize short_pulse/long_pulse within each day
    resume_stage: int = 0               # resume from this stage (0 = start from beginning)\n    keep_raw: bool = False              # keep downloaded raw files after conversion

    # ── Echogram settings ────────────────────────────────────────
    colormap: str = "ocean_r"

    # ── Concurrency ─────────────────────────────────────────────
    parallel_workers: int = 0  # 0 → auto-detect from RAM (2 GB per denoise worker)
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

    def effective_parallel_workers(self, mem_per_worker_gb: float = 2.0) -> int:
        """Return the number of parallel stage workers.

        If ``parallel_workers`` is 0 (the default), auto-detect from total
        system RAM, reserving memory for Dask workers and OS overhead.
        Each parallel denoise/echogram task needs ~2 GB.
        """
        if self.parallel_workers > 0:
            return self.parallel_workers
        try:
            total_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)
        except (ValueError, OSError):
            return 2  # safe default if detection fails
        # Reserve ~50% for the Dask LocalCluster, OS, and headroom
        available_gb = total_gb * 0.5
        workers = max(1, int(available_gb / mem_per_worker_gb))
        # Cap at a reasonable maximum to avoid thrashing
        return min(workers, 16)

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
