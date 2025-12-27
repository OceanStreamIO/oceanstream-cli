"""Unit tests for R2R platform detection from cruise IDs."""

from __future__ import annotations

import pytest

from oceanstream.providers.r2r.r2r import R2RProvider


class TestR2RPlatformDetection:
    """Test R2R platform (vessel) detection from cruise IDs."""

    def test_get_platform_from_cruise_id_roger_revelle(self) -> None:
        """Test detection of R/V Roger Revelle from RR cruise ID."""
        provider = R2RProvider()
        
        # Test various RR cruise IDs
        assert provider.get_platform_from_cruise_id("RR2402") == "R/V Roger Revelle"
        assert provider.get_platform_from_cruise_id("RR2401") == "R/V Roger Revelle"
        assert provider.get_platform_from_cruise_id("RR1234") == "R/V Roger Revelle"
    
    def test_get_platform_from_cruise_id_falkor(self) -> None:
        """Test detection of R/V Falkor from FK cruise ID."""
        provider = R2RProvider()
        
        assert provider.get_platform_from_cruise_id("FK161229") == "R/V Falkor"
        assert provider.get_platform_from_cruise_id("FK170101") == "R/V Falkor"
    
    def test_get_platform_from_cruise_id_atlantis(self) -> None:
        """Test detection of R/V Atlantis from AT cruise ID."""
        provider = R2RProvider()
        
        assert provider.get_platform_from_cruise_id("AT4210") == "R/V Atlantis"
        assert provider.get_platform_from_cruise_id("AT42-10") == "R/V Atlantis"
    
    def test_get_platform_from_cruise_id_palmer(self) -> None:
        """Test detection of RVIB Nathaniel B. Palmer from NBP cruise ID."""
        provider = R2RProvider()
        
        assert provider.get_platform_from_cruise_id("NBP1402") == "RVIB Nathaniel B. Palmer"
        assert provider.get_platform_from_cruise_id("NBP0201") == "RVIB Nathaniel B. Palmer"
    
    def test_get_platform_from_cruise_id_sally_ride(self) -> None:
        """Test detection of R/V Sally Ride from SR/SJ cruise ID."""
        provider = R2RProvider()
        
        # Sally Ride can be SR or SJ
        assert provider.get_platform_from_cruise_id("SR2301") == "R/V Sally Ride"
        assert provider.get_platform_from_cruise_id("SJ2401") == "R/V Sally Ride"
    
    def test_get_platform_from_cruise_id_case_insensitive(self) -> None:
        """Test that cruise ID matching is case insensitive."""
        provider = R2RProvider()
        
        assert provider.get_platform_from_cruise_id("rr2402") == "R/V Roger Revelle"
        assert provider.get_platform_from_cruise_id("Rr2402") == "R/V Roger Revelle"
        assert provider.get_platform_from_cruise_id("fk161229") == "R/V Falkor"
    
    def test_get_platform_from_cruise_id_unknown(self) -> None:
        """Test that unknown vessel codes return None."""
        provider = R2RProvider()
        
        assert provider.get_platform_from_cruise_id("XYZ123") is None
        assert provider.get_platform_from_cruise_id("UNKNOWN2024") is None
    
    def test_get_platform_from_cruise_id_empty_or_none(self) -> None:
        """Test that empty or None cruise IDs return None."""
        provider = R2RProvider()
        
        assert provider.get_platform_from_cruise_id("") is None
        assert provider.get_platform_from_cruise_id(None) is None
    
    def test_get_platform_from_cruise_id_short_code(self) -> None:
        """Test that short cruise IDs still work."""
        provider = R2RProvider()
        
        # Even with just 2-letter code + digits
        assert provider.get_platform_from_cruise_id("RR01") == "R/V Roger Revelle"
        assert provider.get_platform_from_cruise_id("FK01") == "R/V Falkor"
    
    def test_get_platform_from_cruise_id_priority_longest_match(self) -> None:
        """Test that longer vessel codes are matched first."""
        provider = R2RProvider()
        
        # NBP should match 3-letter code, not 2-letter
        assert provider.get_platform_from_cruise_id("NBP1402") == "RVIB Nathaniel B. Palmer"
        
        # If we had both "SK" and "SKQ" codes, SKQ should be checked first
        # (This tests the 4->3->2 letter priority in the implementation)


class TestR2RIdentifyPlatform:
    """Test R2R platform identification from filenames."""
    
    def test_identify_platform_standard_format(self) -> None:
        """Test platform identification from standard R2R filenames."""
        provider = R2RProvider()
        
        assert provider.identify_platform("RR2402_615519_r2rnav.geocsv") == "RR2402"
        assert provider.identify_platform("FK161229_607994_r2rnav.geocsv") == "FK161229"
        assert provider.identify_platform("AT42-10_some_data.geocsv") == "AT42-10"
        assert provider.identify_platform("NBP1402_ctd_001.geocsv") == "NBP1402"
    
    def test_identify_platform_with_hyphen(self) -> None:
        """Test platform identification with hyphenated cruise IDs."""
        provider = R2RProvider()
        
        assert provider.identify_platform("AT42-10_data.geocsv") == "AT42-10"
    
    def test_identify_platform_no_underscore(self) -> None:
        """Test that filenames without underscore return None."""
        provider = R2RProvider()
        
        assert provider.identify_platform("data.geocsv") is None
        assert provider.identify_platform("RR2402.geocsv") is None
    
    def test_identify_platform_invalid_cruise_id_format(self) -> None:
        """Test that invalid cruise ID formats return None."""
        provider = R2RProvider()
        
        # Not enough letters or numbers
        assert provider.identify_platform("R_data.geocsv") is None
        assert provider.identify_platform("12345_data.geocsv") is None
        
        # Invalid characters
        assert provider.identify_platform("R@2402_data.geocsv") is None


class TestR2REnhancedBagInfoParsing:
    """Test enhanced bag-info.txt parsing with R2R-specific fields."""
    
    def test_parse_bag_info_with_r2r_device_type(self, tmp_path) -> None:
        """Test parsing bag-info.txt with R2R-DeviceType field."""
        from oceanstream.providers.r2r_metadata import parse_bag_info
        
        bag_info_path = tmp_path / "bag-info.txt"
        bag_info_path.write_text(
            """R2R-DeviceType: ctd
R2R-DeviceModel: SeaBird SBE-911+
Internal-Sender-Description: CTD data from cruise RR2402
"""
        )
        
        info = parse_bag_info(bag_info_path)
        
        assert info.sensor_type == "ctd"
        assert info.sensor_id == "SeaBird SBE-911+"
        assert info.description == "CTD data from cruise RR2402"
    
    def test_parse_bag_info_with_gnss_device(self, tmp_path) -> None:
        """Test parsing bag-info.txt with GNSS device information."""
        from oceanstream.providers.r2r_metadata import parse_bag_info
        
        bag_info_path = tmp_path / "bag-info.txt"
        bag_info_path.write_text(
            """R2R-ParentDeviceType: gnss
R2R-ParentDeviceModel: com.furuno GP-170
Internal-Sender-Description: Fileset 615519 (r2rnav data) from cruise RR2402
R2R-CruiseID: RR2402
"""
        )
        
        info = parse_bag_info(bag_info_path)
        
        # R2R-ParentDeviceType should not be picked up (we want R2R-DeviceType)
        # but R2R-ParentDeviceModel and description should work
        assert info.description == "Fileset 615519 (r2rnav data) from cruise RR2402"
        assert info.extra is not None
        assert "R2R-CruiseID" in info.extra
        assert info.extra["R2R-CruiseID"] == "RR2402"
    
    def test_parse_bag_info_preserves_extra_fields(self, tmp_path) -> None:
        """Test that bag-info.txt parsing preserves all extra fields."""
        from oceanstream.providers.r2r_metadata import parse_bag_info
        
        bag_info_path = tmp_path / "bag-info.txt"
        bag_info_path.write_text(
            """R2R-DeviceType: fluorometer
R2R-DeviceModel: WET Labs ECO-FLNTU
R2R-CruiseID: FK161229
R2R-FilesetID: 124688
R2R-CruiseDOI: doi:10.7284/123456
Bag-Size: 12.5 MB
"""
        )
        
        info = parse_bag_info(bag_info_path)
        
        assert info.sensor_type == "fluorometer"
        assert info.sensor_id == "WET Labs ECO-FLNTU"
        assert info.extra is not None
        assert info.extra["R2R-CruiseID"] == "FK161229"
        assert info.extra["R2R-FilesetID"] == "124688"
        assert info.extra["R2R-CruiseDOI"] == "doi:10.7284/123456"
        assert info.extra["Bag-Size"] == "12.5 MB"
