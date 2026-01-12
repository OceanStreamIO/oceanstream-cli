"""Unit tests for oceanstream.echodata.config module."""

from pathlib import Path
import pytest
import tempfile

from oceanstream.echodata.config import (
    EchodataConfig,
    DenoiseConfig,
    MVBSConfig,
    NASCConfig,
)


class TestEchodataConfig:
    """Tests for EchodataConfig dataclass."""

    def test_default_values(self):
        """Default config should have sensible values."""
        config = EchodataConfig()
        
        assert config.sonar_model == "EK80"
        assert config.parallel is True
        assert config.n_workers == 4
        assert config.calibration_file is None
        assert config.campaign_id is None

    def test_custom_values(self):
        """Config should accept custom values."""
        config = EchodataConfig(
            sonar_model="EK60",
            parallel=False,
            n_workers=8,
            calibration_file=Path("/path/to/cal.xlsx"),
            campaign_id="TPOS2023",
        )
        
        assert config.sonar_model == "EK60"
        assert config.parallel is False
        assert config.n_workers == 8
        assert config.calibration_file == Path("/path/to/cal.xlsx")
        assert config.campaign_id == "TPOS2023"

    def test_from_toml_missing_file(self, tmp_path: Path):
        """from_toml should return defaults when file is missing."""
        config = EchodataConfig.from_toml(tmp_path / "nonexistent.toml")
        
        assert config.sonar_model == "EK80"  # default

    def test_from_toml_with_file(self, tmp_path: Path):
        """from_toml should load values from TOML file."""
        toml_content = """
[echodata]
sonar_model = "EK60"
parallel = false
n_workers = 2
campaign_id = "TEST_CAMPAIGN"
"""
        toml_file = tmp_path / "oceanstream.toml"
        toml_file.write_text(toml_content)
        
        config = EchodataConfig.from_toml(toml_file)
        
        assert config.sonar_model == "EK60"
        assert config.parallel is False
        assert config.n_workers == 2
        assert config.campaign_id == "TEST_CAMPAIGN"


class TestDenoiseConfig:
    """Tests for DenoiseConfig dataclass."""

    def test_default_values(self):
        """Default denoise config should include all methods."""
        config = DenoiseConfig()
        
        assert "background" in config.methods
        assert "transient" in config.methods
        assert "impulse" in config.methods
        assert "attenuation" in config.methods

    def test_background_defaults(self):
        """Background noise removal defaults."""
        config = DenoiseConfig()
        
        assert config.background_num_side_pings == 25
        assert config.background_noise_max is None  # dB

    def test_transient_defaults(self):
        """Transient noise removal defaults."""
        config = DenoiseConfig()
        
        assert config.transient_a == 2.0
        assert config.transient_n == 5

    def test_impulse_defaults(self):
        """Impulse noise removal defaults."""
        config = DenoiseConfig()
        
        assert config.impulse_threshold_db == 10.0
        assert config.impulse_num_lags == 3

    def test_attenuation_defaults(self):
        """Attenuation detection defaults."""
        config = DenoiseConfig()
        
        assert config.attenuation_threshold == 0.8

    def test_custom_methods(self):
        """Config should accept custom method list."""
        config = DenoiseConfig(methods=["background", "impulse"])
        
        assert config.methods == ["background", "impulse"]
        assert "transient" not in config.methods


class TestMVBSConfig:
    """Tests for MVBSConfig dataclass."""

    def test_default_values(self):
        """Default MVBS config should have standard bin sizes."""
        config = MVBSConfig()
        
        assert config.range_bin == "1m"
        assert config.ping_time_bin == "5s"

    def test_custom_bins(self):
        """Config should accept custom bin sizes."""
        config = MVBSConfig(
            range_bin="5m",
            ping_time_bin="10s",
        )
        
        assert config.range_bin == "5m"
        assert config.ping_time_bin == "10s"


class TestNASCConfig:
    """Tests for NASCConfig dataclass."""

    def test_default_values(self):
        """Default NASC config should have ICES-standard defaults."""
        config = NASCConfig()
        
        # ICES-standard: 10m vertical, 0.5 nautical mile horizontal
        assert config.range_bin == "10m"
        assert config.dist_bin == "0.5nmi"

    def test_custom_bins(self):
        """Config should accept custom bin sizes."""
        config = NASCConfig(
            range_bin="20m",
            dist_bin="1nmi",
        )
        
        assert config.range_bin == "20m"
        assert config.dist_bin == "1nmi"


class TestConfigIntegration:
    """Integration tests for config loading."""

    def test_full_toml_config(self, tmp_path: Path):
        """Test loading complete config from TOML."""
        toml_content = """
[echodata]
sonar_model = "EK80"
parallel = true
n_workers = 8
campaign_id = "TPOS2023"

[echodata.denoise]
methods = ["background", "impulse"]
background_num_side_pings = 30
impulse_threshold_db = 12.0

[echodata.mvbs]
range_bin = "2m"
ping_time_bin = "10s"

[echodata.nasc]
range_bin = "15m"
dist_bin = "1nmi"
"""
        toml_file = tmp_path / "oceanstream.toml"
        toml_file.write_text(toml_content)
        
        config = EchodataConfig.from_toml(toml_file)
        
        assert config.sonar_model == "EK80"
        assert config.n_workers == 8
        assert config.campaign_id == "TPOS2023"
