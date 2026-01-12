"""E2E tests for environment package - sound speed and absorption calculations."""

from pathlib import Path
import pytest
import numpy as np


@pytest.mark.e2e
class TestSoundSpeedE2E:
    """End-to-end tests for sound speed calculations."""

    def test_mackenzie_typical_ocean_conditions(self):
        """Test Mackenzie formula with typical ocean conditions."""
        from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
        
        # Typical surface ocean: 15°C, 35 PSU, 10m depth
        speed = mackenzie_sound_speed(15.0, 35.0, 10.0)
        
        # Expected ~1507 m/s for these conditions
        assert 1500 < speed < 1520, f"Sound speed {speed} outside expected range"
    
    def test_mackenzie_deep_cold_water(self):
        """Test Mackenzie at depth with cold water."""
        from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
        
        # Deep cold water: 2°C, 35 PSU, 1000m
        speed = mackenzie_sound_speed(2.0, 35.0, 1000.0)
        
        # Pressure effect increases speed at depth despite cold temp
        assert 1470 < speed < 1510, f"Sound speed {speed} outside expected range"
    
    def test_mackenzie_warm_shallow_water(self):
        """Test Mackenzie in warm shallow water."""
        from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
        
        # Warm shallow: 25°C, 36 PSU, 5m
        speed = mackenzie_sound_speed(25.0, 36.0, 5.0)
        
        # Higher temp = faster sound
        assert 1530 < speed < 1560, f"Sound speed {speed} outside expected range"
    
    def test_chen_millero_matches_mackenzie_approximately(self):
        """Chen-Millero and Mackenzie should give similar results."""
        from oceanstream.echodata.environment.sound_speed import (
            mackenzie_sound_speed,
            chen_millero_sound_speed,
        )
        
        # Standard conditions
        temp, sal, depth = 15.0, 35.0, 100.0
        
        speed_mackenzie = mackenzie_sound_speed(temp, sal, depth)
        speed_chen_millero = chen_millero_sound_speed(temp, sal, depth)
        
        # Should be within ~2 m/s of each other
        diff = abs(speed_mackenzie - speed_chen_millero)
        assert diff < 2.0, f"Mackenzie ({speed_mackenzie}) and Chen-Millero ({speed_chen_millero}) differ by {diff} m/s"
    
    def test_compute_sound_speed_dispatcher(self):
        """Test the compute_sound_speed dispatcher function."""
        from oceanstream.echodata.environment.sound_speed import compute_sound_speed
        
        temp, sal, depth = 15.0, 35.0, 50.0
        
        speed_cm = compute_sound_speed(temp, sal, depth, method="chen_millero")
        speed_mack = compute_sound_speed(temp, sal, depth, method="mackenzie")
        
        assert 1500 < speed_cm < 1520
        assert 1500 < speed_mack < 1520
    
    def test_sound_speed_array_input(self):
        """Sound speed functions should handle array inputs."""
        from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
        
        temps = np.array([10.0, 15.0, 20.0])
        sals = np.array([34.0, 35.0, 36.0])
        depths = np.array([10.0, 50.0, 100.0])
        
        speeds = mackenzie_sound_speed(temps, sals, depths)
        
        assert len(speeds) == 3
        # Sound speed increases with temperature
        assert speeds[0] < speeds[1] < speeds[2]


@pytest.mark.e2e
class TestAbsorptionE2E:
    """End-to-end tests for absorption coefficient calculations."""

    def test_absorption_38khz(self):
        """Test absorption at 38 kHz (common echosounder frequency)."""
        from oceanstream.echodata.environment.absorption import compute_absorption_coefficient
        
        # Typical conditions: 15°C, 35 PSU, 100m
        alpha = compute_absorption_coefficient(
            frequency_hz=38000,
            temperature=15.0,
            salinity=35.0,
            depth=100.0,
        )
        
        # At 38 kHz, typical absorption is ~8-10 dB/km = 0.008-0.010 dB/m
        assert 0.005 < alpha < 0.015, f"38 kHz absorption should be ~0.01 dB/m, got {alpha}"
    
    def test_absorption_120khz(self):
        """Test absorption at 120 kHz."""
        from oceanstream.echodata.environment.absorption import compute_absorption_coefficient
        
        alpha = compute_absorption_coefficient(
            frequency_hz=120000,
            temperature=15.0,
            salinity=35.0,
            depth=100.0,
        )
        
        # At 120 kHz, typical absorption is ~35-45 dB/km = 0.035-0.045 dB/m
        assert 0.025 < alpha < 0.055, f"120 kHz absorption should be ~0.04 dB/m, got {alpha}"
    
    def test_absorption_200khz(self):
        """Test absorption at 200 kHz."""
        from oceanstream.echodata.environment.absorption import compute_absorption_coefficient
        
        alpha = compute_absorption_coefficient(
            frequency_hz=200000,
            temperature=15.0,
            salinity=35.0,
            depth=100.0,
        )
        
        # At 200 kHz, typical absorption is ~70-90 dB/km = 0.07-0.09 dB/m
        assert 0.05 < alpha < 0.12, f"200 kHz absorption should be ~0.08 dB/m, got {alpha}"
    
    def test_absorption_increases_with_frequency(self):
        """Absorption should increase with frequency."""
        from oceanstream.echodata.environment.absorption import compute_absorption_coefficient
        
        freqs = [18000, 38000, 70000, 120000, 200000]
        absorptions = []
        
        for f in freqs:
            alpha = compute_absorption_coefficient(
                frequency_hz=f,
                temperature=15.0,
                salinity=35.0,
                depth=50.0,
            )
            absorptions.append(alpha)
        
        # Each frequency should have higher absorption than the previous
        for i in range(1, len(absorptions)):
            assert absorptions[i] > absorptions[i-1], \
                f"Absorption at {freqs[i]}Hz ({absorptions[i]}) should be > at {freqs[i-1]}Hz ({absorptions[i-1]})"
    
    def test_absorption_array_input(self):
        """Absorption should handle array inputs."""
        from oceanstream.echodata.environment.absorption import compute_absorption_coefficient
        
        temps = np.array([10.0, 15.0, 20.0])
        sals = np.array([34.0, 35.0, 36.0])
        depths = np.array([50.0, 100.0, 150.0])
        
        alphas = compute_absorption_coefficient(
            frequency_hz=38000,
            temperature=temps,
            salinity=sals,
            depth=depths,
        )
        
        assert len(alphas) == 3
        assert all(alpha > 0 for alpha in alphas)


@pytest.mark.e2e
class TestSoundSpeedFromCopernicusE2E:
    """End-to-end tests for sound speed computation from Copernicus-style data."""

    def test_compute_sound_speed_from_mock_copernicus(self):
        """Test compute_sound_speed_from_copernicus with mock data."""
        import xarray as xr
        from oceanstream.echodata.environment.sound_speed import compute_sound_speed_from_copernicus
        
        # Create mock Copernicus-style dataset
        depths = np.array([0.5, 5.0, 10.0, 20.0, 50.0, 100.0])
        mock_ds = xr.Dataset(
            {
                "thetao": ("depth", [18.0, 17.5, 17.0, 16.0, 14.0, 12.0]),  # potential temp
                "so": ("depth", [35.0, 35.1, 35.2, 35.3, 35.4, 35.5]),     # salinity
            },
            coords={"depth": depths},
        )
        
        # Compute sound speed at 5m depth
        sound_speed, temp = compute_sound_speed_from_copernicus(
            mock_ds,
            latitude=10.0,
            target_depth=5.0,
        )
        
        # Check results are reasonable
        assert 1500 < sound_speed < 1550, f"Sound speed {sound_speed} outside range"
        assert 15 < temp < 20, f"Temperature {temp} outside range"
    
    def test_copernicus_sound_speed_depth_selection(self):
        """Test that nearest depth is correctly selected."""
        import xarray as xr
        from oceanstream.echodata.environment.sound_speed import compute_sound_speed_from_copernicus
        
        depths = np.array([1.0, 5.0, 10.0, 25.0, 50.0])
        mock_ds = xr.Dataset(
            {
                "thetao": ("depth", [20.0, 18.0, 16.0, 14.0, 12.0]),
                "so": ("depth", [35.0, 35.0, 35.0, 35.0, 35.0]),
            },
            coords={"depth": depths},
        )
        
        # Request 7m - should select 5m (nearest)
        speed_5m, temp_5m = compute_sound_speed_from_copernicus(mock_ds, latitude=0.0, target_depth=7.0)
        
        # Request 12m - should select 10m (nearest)
        speed_10m, temp_10m = compute_sound_speed_from_copernicus(mock_ds, latitude=0.0, target_depth=12.0)
        
        # Warmer water (5m) should have faster sound speed
        assert speed_5m > speed_10m, "Warmer shallow water should have faster sound speed"
        assert temp_5m > temp_10m, "Shallower should be warmer"


@pytest.mark.e2e
class TestEnvironmentIntegrationE2E:
    """Integration tests combining sound speed and absorption."""

    def test_full_environment_calculation(self):
        """Test computing both sound speed and absorption for a profile."""
        from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
        from oceanstream.echodata.environment.absorption import compute_absorption_coefficient
        
        # Depth profile
        depths = np.array([5.0, 10.0, 25.0, 50.0, 100.0, 200.0])
        temps = np.array([18.0, 17.5, 16.0, 14.0, 12.0, 10.0])  # Thermocline
        sals = np.array([35.0, 35.0, 35.1, 35.2, 35.3, 35.4])
        
        # Compute sound speed profile
        sound_speeds = mackenzie_sound_speed(temps, sals, depths)
        
        # Compute absorption at 38 kHz for each depth
        absorptions = compute_absorption_coefficient(
            frequency_hz=38000,
            temperature=temps,
            salinity=sals,
            depth=depths,
        )
        
        # Sound speed should decrease with depth (temp dominates)
        assert sound_speeds[0] > sound_speeds[-1], "Sound speed should decrease with cooling"
        
        # All values should be physically reasonable
        assert all(1450 < s < 1560 for s in sound_speeds)
        # Absorption should be positive and finite
        assert all(a > 0 and np.isfinite(a) for a in absorptions)
    
    def test_environment_for_saildrone_conditions(self):
        """Test environment calculations for typical Saildrone conditions."""
        from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed
        from oceanstream.echodata.environment.absorption import compute_absorption_coefficient
        
        # Saildrone SBE37 measures at ~0.6m depth
        # Typical tropical Pacific conditions from TPOS campaign
        temp = 28.0  # Warm surface
        sal = 35.5
        depth = 0.6
        
        speed = mackenzie_sound_speed(temp, sal, depth)
        
        # 38 kHz absorption
        alpha_38 = compute_absorption_coefficient(38000, temp, sal, depth)
        # 200 kHz absorption
        alpha_200 = compute_absorption_coefficient(200000, temp, sal, depth)
        
        # Warm water has faster sound speed
        assert speed > 1535, f"Expected faster sound in warm water, got {speed}"
        
        # Check absorption ratio is reasonable (200kHz / 38kHz)
        # At warm temperatures the ratio can be higher
        ratio = alpha_200 / alpha_38
        assert 3 < ratio < 20, f"200/38 kHz absorption ratio {ratio} unusual"
