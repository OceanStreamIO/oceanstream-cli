from providers import get_provider
import pandas as pd


def test_saildrone_semantic_alias_mapping():
    """Test semantic mappings for Saildrone-specific abbreviations."""
    provider = get_provider("saildrone")
    cols = ["SOG", "COG", "HDG", "TEMP_AIR_MEAN", "CHLOR_WETLABS_MEAN"]
    aliases = provider.alias_mapping(cols)
    
    # Verify semantic mappings to canonical vocabulary
    assert aliases["SOG"] == "speed_over_ground_ms"
    assert aliases["COG"] == "course_over_ground_deg"
    assert aliases["HDG"] == "heading_deg"
    assert aliases["TEMP_AIR_MEAN"] == "air_temperature_mean_c"
    assert aliases["CHLOR_WETLABS_MEAN"] == "chlorophyll_fluorescence_mean_ug_l"


def test_saildrone_syntactic_alias_mapping():
    """Test syntactic normalization for columns without semantic mappings."""
    provider = get_provider("saildrone")
    cols = ["latitude", "longitude", "CustomColumn", "UNKNOWN_FIELD"]
    aliases = provider.alias_mapping(cols)
    
    # latitude/longitude should not change (already lowercase)
    assert "latitude" not in aliases
    assert "longitude" not in aliases
    
    # Syntactic normalization for unmapped columns
    assert aliases["CustomColumn"] == "custom_column"
    assert aliases["UNKNOWN_FIELD"] == "unknown_field"


def test_saildrone_alias_mapping_priority():
    """Test that semantic mappings take priority over syntactic."""
    provider = get_provider("saildrone")
    cols = ["SOG", "TEMP_AIR_MEAN"]
    aliases = provider.alias_mapping(cols)
    
    # Should use semantic mapping, not just lowercase
    assert aliases["SOG"] == "speed_over_ground_ms"  # NOT "sog"
    assert aliases["TEMP_AIR_MEAN"] == "air_temperature_mean_c"  # NOT "temp_air_mean"


def test_enrich_dataframe_converts_time():
    provider = get_provider("saildrone")
    df = pd.DataFrame({
        "platform_id": ["sd1030"],
        "latitude": [10.0],
        "longitude": [-150.0],
        "time": ["2025-11-07T12:00:00Z"],
        "SOG": [5.2]
    })
    enriched = provider.enrich_dataframe(df)
    assert enriched["time"].dtype.kind == "M"  # datetime64
