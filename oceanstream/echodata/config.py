"""Configuration classes for echodata processing.

These dataclasses define all configuration options for the echodata processing
pipeline, loadable from oceanstream.toml or programmatically configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any
import os


@dataclass
class DenoiseConfig:
    """Configuration for denoising pipeline.
    
    Based on De Robertis & Higginbottom (2007) and Fielding et al. algorithms.
    """
    
    methods: list[str] = field(
        default_factory=lambda: ["background", "transient", "impulse", "attenuation"]
    )
    
    # Background noise (De Robertis & Higginbottom 2007)
    background_num_side_pings: int = 25  # Number of pings on each side
    background_range_window: int = 20
    background_ping_window: int = 50
    background_snr_threshold: float = 3.0  # dB
    background_noise_max: Optional[float] = None  # dB, None = no threshold
    
    # Transient noise (Fielding et al.)
    transient_a: float = 2.0  # Threshold multiplier
    transient_n: int = 5  # Number of neighboring pings
    transient_exclude_above: float = 250.0  # m
    transient_depth_bin: float = 5.0  # m
    transient_n_pings: int = 20
    transient_threshold_db: float = 6.0
    
    # Impulse noise
    impulse_threshold_db: float = 10.0
    impulse_num_lags: int = 3  # Number of lag comparisons
    impulse_vertical_bin: float = 2.0  # m
    impulse_ping_lags: list[int] = field(default_factory=lambda: [1])
    
    # Attenuation
    attenuation_threshold: float = 0.8  # Correlation threshold
    attenuation_upper_limit: float = 180.0  # m
    attenuation_lower_limit: float = 280.0  # m
    attenuation_side_pings: int = 15
    
    def to_background_params(self) -> dict:
        """Return parameters dict for background_noise_mask function."""
        return {
            "range_window": self.background_range_window,
            "ping_window": self.background_ping_window,
            "SNR_threshold": f"{self.background_snr_threshold}dB",
            "background_noise_max": f"{self.background_noise_max}dB",
        }
    
    def to_transient_params(self) -> dict:
        """Return parameters dict for transient_noise_mask function."""
        return {
            "exclude_above": self.transient_exclude_above,
            "depth_bin": self.transient_depth_bin,
            "n_pings": self.transient_n_pings,
            "thr_dB": self.transient_threshold_db,
        }
    
    def to_impulse_params(self) -> dict:
        """Return parameters dict for impulse_noise_mask function."""
        return {
            "vertical_bin_size": self.impulse_vertical_bin,
            "ping_lags": self.impulse_ping_lags,
            "threshold_db": self.impulse_threshold_db,
        }
    
    def to_attenuation_params(self) -> dict:
        """Return parameters dict for attenuation_mask function."""
        return {
            "upper_limit_sl": self.attenuation_upper_limit,
            "lower_limit_sl": self.attenuation_lower_limit,
            "num_side_pings": self.attenuation_side_pings,
            "threshold": self.attenuation_threshold,
        }


@dataclass
class MVBSConfig:
    """Configuration for MVBS (Mean Volume Backscattering Strength) computation."""
    
    range_bin: str = "1m"
    ping_time_bin: str = "5s"
    
    def to_echopype_kwargs(self) -> dict:
        """Return kwargs for echopype.commongrid.compute_MVBS."""
        return {
            "range_bin": self.range_bin,
            "ping_time_bin": self.ping_time_bin,
        }


@dataclass
class NASCConfig:
    """Configuration for NASC (Nautical Area Scattering Coefficient) computation."""
    
    range_bin: str = "10m"
    dist_bin: str = "0.5nmi"
    
    def to_echopype_kwargs(self) -> dict:
        """Return kwargs for echopype.commongrid.compute_NASC."""
        return {
            "range_bin": self.range_bin,
            "dist_bin": self.dist_bin,
        }


@dataclass
class EchodataConfig:
    """Main configuration for echodata processing.
    
    Can be loaded from oceanstream.toml or created programmatically.
    
    Example:
        config = EchodataConfig.from_toml(Path("oceanstream.toml"))
        config = EchodataConfig(sonar_model="EK60", parallel=False)
    """
    
    sonar_model: str = "EK80"
    calibration_file: Optional[Path] = None
    campaign_id: Optional[str] = None
    parallel: bool = True
    n_workers: int = 4
    use_dask: bool = True
    dask_chunks: dict = field(
        default_factory=lambda: {"ping_time": 2000, "range_sample": -1}
    )
    
    # Environmental data settings
    use_copernicus_fallback: bool = True
    copernicus_days_back: int = 10  # Days to average for Copernicus data
    
    # Sub-configurations
    denoise: DenoiseConfig = field(default_factory=DenoiseConfig)
    mvbs: MVBSConfig = field(default_factory=MVBSConfig)
    nasc: NASCConfig = field(default_factory=NASCConfig)
    
    def __post_init__(self):
        """Expand environment variables in paths."""
        if self.calibration_file is not None:
            if isinstance(self.calibration_file, str):
                expanded = os.path.expandvars(self.calibration_file)
                self.calibration_file = Path(expanded)
    
    @classmethod
    def from_toml(cls, path: Path) -> "EchodataConfig":
        """Load configuration from oceanstream.toml file.
        
        Args:
            path: Path to oceanstream.toml file
            
        Returns:
            EchodataConfig instance with values from TOML
        """
        if not path.exists():
            return cls()
        
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # Python < 3.11
        
        with open(path, "rb") as f:
            data = tomllib.load(f)
        
        echodata = data.get("echodata", {})
        
        # Create defaults for reference
        default_denoise = DenoiseConfig()
        default_mvbs = MVBSConfig()
        default_nasc = NASCConfig()
        
        # Parse denoise config
        denoise_data = echodata.get("denoise", {})
        denoise_config = DenoiseConfig(
            methods=denoise_data.get("methods", default_denoise.methods),
            background_num_side_pings=denoise_data.get(
                "background", {}
            ).get("num_side_pings", default_denoise.background_num_side_pings),
            background_range_window=denoise_data.get(
                "background", {}
            ).get("range_window", default_denoise.background_range_window),
            background_ping_window=denoise_data.get(
                "background", {}
            ).get("ping_window", default_denoise.background_ping_window),
            background_snr_threshold=_parse_db(
                denoise_data.get("background", {}).get(
                    "SNR_threshold", default_denoise.background_snr_threshold
                )
            ),
            background_noise_max=_parse_db_optional(
                denoise_data.get("background", {}).get(
                    "background_noise_max", default_denoise.background_noise_max
                )
            ),
            transient_a=denoise_data.get(
                "transient", {}
            ).get("a", default_denoise.transient_a),
            transient_n=denoise_data.get(
                "transient", {}
            ).get("n", default_denoise.transient_n),
            transient_exclude_above=denoise_data.get(
                "transient", {}
            ).get("exclude_above", default_denoise.transient_exclude_above),
            transient_depth_bin=denoise_data.get(
                "transient", {}
            ).get("depth_bin", default_denoise.transient_depth_bin),
            transient_n_pings=denoise_data.get(
                "transient", {}
            ).get("n_pings", default_denoise.transient_n_pings),
            transient_threshold_db=denoise_data.get(
                "transient", {}
            ).get("thr_dB", default_denoise.transient_threshold_db),
            impulse_threshold_db=denoise_data.get(
                "impulse", {}
            ).get("threshold_db", default_denoise.impulse_threshold_db),
            impulse_num_lags=denoise_data.get(
                "impulse", {}
            ).get("num_lags", default_denoise.impulse_num_lags),
            impulse_vertical_bin=_parse_meters(
                denoise_data.get("impulse", {}).get(
                    "vertical_bin_size", default_denoise.impulse_vertical_bin
                )
            ),
            impulse_ping_lags=denoise_data.get(
                "impulse", {}
            ).get("ping_lags", default_denoise.impulse_ping_lags),
            attenuation_threshold=denoise_data.get(
                "attenuation", {}
            ).get("threshold", default_denoise.attenuation_threshold),
            attenuation_upper_limit=denoise_data.get(
                "attenuation", {}
            ).get("upper_limit_sl", default_denoise.attenuation_upper_limit),
            attenuation_lower_limit=denoise_data.get(
                "attenuation", {}
            ).get("lower_limit_sl", default_denoise.attenuation_lower_limit),
            attenuation_side_pings=denoise_data.get(
                "attenuation", {}
            ).get("num_side_pings", default_denoise.attenuation_side_pings),
        )
        
        # Parse MVBS config
        mvbs_data = echodata.get("mvbs", {})
        mvbs_config = MVBSConfig(
            range_bin=mvbs_data.get("range_bin", default_mvbs.range_bin),
            ping_time_bin=mvbs_data.get("ping_time_bin", default_mvbs.ping_time_bin),
        )
        
        # Parse NASC config
        nasc_data = echodata.get("nasc", {})
        nasc_config = NASCConfig(
            range_bin=nasc_data.get("range_bin", default_nasc.range_bin),
            dist_bin=nasc_data.get("dist_bin", default_nasc.dist_bin),
        )
        
        return cls(
            sonar_model=echodata.get("sonar_model", "EK80"),
            calibration_file=(
                Path(echodata["calibration_file"])
                if "calibration_file" in echodata
                else None
            ),
            campaign_id=echodata.get("campaign_id"),
            parallel=echodata.get("parallel", True),
            n_workers=echodata.get("n_workers", 4),
            dask_chunks=echodata.get("chunks", {"ping_time": 2000}),
            use_copernicus_fallback=echodata.get("use_copernicus_fallback", True),
            copernicus_days_back=echodata.get("copernicus_days_back", 10),
            denoise=denoise_config,
            mvbs=mvbs_config,
            nasc=nasc_config,
        )
    
    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary for serialization."""
        return {
            "sonar_model": self.sonar_model,
            "calibration_file": str(self.calibration_file) if self.calibration_file else None,
            "use_dask": self.use_dask,
            "dask_chunks": self.dask_chunks,
            "use_copernicus_fallback": self.use_copernicus_fallback,
            "copernicus_days_back": self.copernicus_days_back,
            "denoise": {
                "methods": self.denoise.methods,
                "background": self.denoise.to_background_params(),
                "transient": self.denoise.to_transient_params(),
                "impulse": self.denoise.to_impulse_params(),
                "attenuation": self.denoise.to_attenuation_params(),
            },
            "mvbs": self.mvbs.to_echopype_kwargs(),
            "nasc": self.nasc.to_echopype_kwargs(),
        }


def _parse_db(value: Any) -> float:
    """Parse a dB value, stripping 'dB' suffix if present."""
    if isinstance(value, str):
        return float(value.replace("dB", "").strip())
    return float(value)


def _parse_db_optional(value: Any) -> Optional[float]:
    """Parse a dB value, returning None if value is None."""
    if value is None:
        return None
    if isinstance(value, str):
        return float(value.replace("dB", "").strip())
    return float(value)


def _parse_meters(value: Any) -> float:
    """Parse a meter value, stripping 'm' suffix if present."""
    if isinstance(value, str):
        return float(value.replace("m", "").strip())
    return float(value)
