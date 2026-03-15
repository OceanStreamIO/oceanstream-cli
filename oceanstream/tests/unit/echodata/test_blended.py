"""Tests for oceanstream.echodata.environment.blended.

Pure numpy/physics tests — uses Mackenzie and Francois-Garrison equations
directly; no echopype dependency.
"""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.echodata.environment.blended import (
    build_blended_profile,
    compute_depth_weighted_env_params,
)


@pytest.fixture()
def copernicus_profile():
    """Synthetic Copernicus depth profile for testing."""
    return {
        "depth": [0.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0],
        "temperature": [25.0, 24.5, 23.0, 20.0, 15.0, 10.0, 5.0],
        "salinity": [35.0, 35.0, 35.1, 35.2, 35.3, 35.4, 35.5],
    }


# ---------------------------------------------------------------------------
# build_blended_profile
# ---------------------------------------------------------------------------

class TestBuildBlendedProfile:
    """Tests for blended T/S/c profile construction."""

    def test_returns_required_keys(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=26.0,
            insitu_sal=34.8,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        for key in ("depth", "temperature", "salinity", "sound_speed", "source"):
            assert key in profile

    def test_first_entry_is_insitu(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=26.0,
            insitu_sal=34.8,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        assert profile["depth"][0] == 0.5
        assert profile["temperature"][0] == 26.0
        assert profile["salinity"][0] == 34.8
        assert profile["source"][0] == "insitu"

    def test_copernicus_below_blend_depth(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=26.0,
            insitu_sal=34.8,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
            blend_depth=5.0,
        )
        # Entries at ≥5m should come from copernicus
        cop_sources = [s for s in profile["source"] if s == "copernicus"]
        assert len(cop_sources) > 0
        # All copernicus entries should be at ≥5m
        for d, s in zip(profile["depth"], profile["source"]):
            if s == "copernicus":
                assert d >= 5.0

    def test_sound_speed_physically_reasonable(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=25.0,
            insitu_sal=35.0,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        # Sound speed in seawater should be ~1400–1600 m/s
        for c in profile["sound_speed"]:
            assert 1400 < c < 1600, f"Sound speed {c} m/s out of physical range"

    def test_custom_blend_depth(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=26.0,
            insitu_sal=34.8,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
            blend_depth=50.0,
        )
        # Should only include copernicus depths ≥50m
        cop_depths = [d for d, s in zip(profile["depth"], profile["source"]) if s == "copernicus"]
        for d in cop_depths:
            assert d >= 50.0

    def test_monotonically_increasing_depth(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=26.0,
            insitu_sal=34.8,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        depths = profile["depth"]
        for i in range(1, len(depths)):
            assert depths[i] >= depths[i - 1]


# ---------------------------------------------------------------------------
# compute_depth_weighted_env_params
# ---------------------------------------------------------------------------

class TestComputeDepthWeightedEnvParams:
    """Tests for effective sound speed and absorption computation."""

    def test_returns_two_floats(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=25.0,
            insitu_sal=35.0,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        c_eff, alpha_eff = compute_depth_weighted_env_params(
            profile, target_depth=100.0, frequency_hz=38000,
        )
        assert isinstance(c_eff, float)
        assert isinstance(alpha_eff, float)

    def test_sound_speed_in_range(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=25.0,
            insitu_sal=35.0,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        c_eff, _ = compute_depth_weighted_env_params(
            profile, target_depth=100.0, frequency_hz=38000,
        )
        assert 1400 < c_eff < 1600

    def test_absorption_positive(self, copernicus_profile):
        profile = build_blended_profile(
            insitu_temp=25.0,
            insitu_sal=35.0,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        _, alpha_eff = compute_depth_weighted_env_params(
            profile, target_depth=50.0, frequency_hz=38000,
        )
        assert alpha_eff > 0

    def test_shallow_vs_deep_target(self, copernicus_profile):
        """Deeper targets should have different effective params due to thermocline."""
        profile = build_blended_profile(
            insitu_temp=25.0,
            insitu_sal=35.0,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        c_shallow, _ = compute_depth_weighted_env_params(
            profile, target_depth=10.0, frequency_hz=38000,
        )
        c_deep, _ = compute_depth_weighted_env_params(
            profile, target_depth=200.0, frequency_hz=38000,
        )
        # With a strong thermocline, effective c should differ
        assert c_shallow != c_deep

    def test_frequency_affects_absorption(self, copernicus_profile):
        """Higher frequency → higher absorption."""
        profile = build_blended_profile(
            insitu_temp=25.0,
            insitu_sal=35.0,
            insitu_depth=0.5,
            copernicus_profile=copernicus_profile,
        )
        _, alpha_38k = compute_depth_weighted_env_params(
            profile, target_depth=100.0, frequency_hz=38000,
        )
        _, alpha_200k = compute_depth_weighted_env_params(
            profile, target_depth=100.0, frequency_hz=200000,
        )
        assert alpha_200k > alpha_38k
