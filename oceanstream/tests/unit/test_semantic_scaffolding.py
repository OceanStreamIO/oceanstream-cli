import pandas as pd
from oceanstream.semantic import SemanticMapper, SemanticConfig


def test_semantic_mapper_basic_alias_and_cf_mapping():
    df = pd.DataFrame({
        "TEMP_SBE37_MEAN": [20.1, 20.2],
        "SAL_SBE37_MEAN": [33.1, 33.0],
        "latitude": [1.0, 1.1],
        "longitude": [2.0, 2.1],
    })
    # Configure with a tiny in-memory alias mapping: alias_table_path not used; inject via config disabled paths
    cfg = SemanticConfig(enabled=True, cf_table_path=None, alias_table_path=None, rename_columns=False)
    mapper = SemanticMapper(cfg)
    # monkeypatch aliases and CF table for the test
    mapper._aliases = {
        # normalized keys
        "temp_sbe37_mean": "sea_water_temperature",
        "sal_sbe37_mean": "sea_water_salinity",
    }
    mapper._cf_table = {"sea_water_temperature", "sea_water_salinity"}

    result = mapper.apply(df)
    # Expect alias mapping produced for these variables
    assert result.canonical_mapping["TEMP_SBE37_MEAN"] == "sea_water_temperature"
    assert result.canonical_mapping["SAL_SBE37_MEAN"] == "sea_water_salinity"

    # CF mapping should be exact with confidence 1.0
    assert result.cf_mapping["TEMP_SBE37_MEAN"]["cf_standard_name"] == "sea_water_temperature"
    assert result.cf_mapping["TEMP_SBE37_MEAN"]["confidence"] == 1.0

    # Units inferred heuristically for temperature/salinity
    assert result.units["sea_water_temperature"] == "degC"
    assert result.units["sea_water_salinity"] == "1e-3"
