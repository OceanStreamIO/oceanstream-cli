"""Tests for built-in semantic tables."""
import json
from pathlib import Path
import pytest
import pandas as pd
from oceanstream.semantic.semantic import SemanticConfig, SemanticMapper


class TestBuiltinTables:
    """Tests for built-in CF and alias tables."""

    def test_builtin_cf_table_exists(self):
        """Test that built-in CF table exists and is valid JSON."""
        cf_path = Path(__file__).parent.parent.parent / "semantic" / "data" / "cf-standard-names.json"
        assert cf_path.exists(), f"Built-in CF table not found: {cf_path}"
        
        with open(cf_path) as f:
            cf_data = json.load(f)
        
        assert isinstance(cf_data, list), "CF table should be a JSON array"
        assert len(cf_data) > 0, "CF table should not be empty"
        
        # Check for common oceanographic terms
        assert "sea_water_temperature" in cf_data
        assert "sea_water_practical_salinity" in cf_data
        assert "air_temperature" in cf_data
        assert "wind_speed" in cf_data
        
        print(f"✓ Built-in CF table contains {len(cf_data)} standard names")

    def test_builtin_alias_table_exists(self):
        """Test that built-in Saildrone alias table exists and is valid JSON."""
        alias_path = Path(__file__).parent.parent.parent / "semantic" / "data" / "saildrone-aliases.json"
        assert alias_path.exists(), f"Built-in alias table not found: {alias_path}"
        
        with open(alias_path) as f:
            alias_data = json.load(f)
        
        assert isinstance(alias_data, dict), "Alias table should be a JSON dictionary"
        assert len(alias_data) > 0, "Alias table should not be empty"
        
        # Check for common Saildrone columns
        assert "sea_water_temperature" in alias_data
        temp_aliases = alias_data["sea_water_temperature"]
        assert isinstance(temp_aliases, list)
        assert "TEMP_CTD_RBR_MEAN" in temp_aliases
        assert "sst" in temp_aliases
        
        print(f"✓ Built-in alias table contains {len(alias_data)} canonical mappings")

    def test_builtin_tables_load_automatically(self):
        """Test that built-in tables are loaded when no custom paths provided."""
        # Create config without specifying table paths
        config = SemanticConfig(
            enabled=True,
            cf_table_path=None,
            alias_table_path=None,
            min_confidence=0.7
        )
        
        mapper = SemanticMapper(config)
        
        # Check that CF table was loaded
        assert len(mapper._cf_table) > 0, "Built-in CF table should be loaded"
        assert "sea_water_temperature" in mapper._cf_table
        
        # Check that alias table was loaded
        assert len(mapper._aliases) > 0, "Built-in alias table should be loaded"
        # Aliases are normalized to lowercase
        assert "temp_ctd_rbr_mean" in mapper._aliases
        
        print(f"✓ Loaded {len(mapper._cf_table)} CF names and {len(mapper._aliases)} aliases automatically")

    def test_saildrone_column_mapping(self):
        """Test that common Saildrone columns are mapped correctly."""
        config = SemanticConfig(enabled=True, min_confidence=0.7)
        mapper = SemanticMapper(config)
        
        # Sample DataFrame with Saildrone column names
        df = pd.DataFrame({
            'time': ['2024-01-01T00:00:00Z'],
            'latitude': [37.7749],
            'longitude': [-122.4194],
            'TEMP_CTD_RBR_MEAN': [15.3],
            'SAL_RBR_MEAN': [33.5],
            'BARO_PRES_MEAN': [1013.25],
            'WSPD_MEAN': [5.2],
            'WDIR_MEAN': [270.0],
            'CHLA_WETLABS_MEAN': [2.5],
            'O2_CONC_RBR_MEAN': [8.1]
        })
        
        result = mapper.apply(df)
        
        # Check canonical mappings
        assert 'TEMP_CTD_RBR_MEAN' in result.canonical_mapping
        assert result.canonical_mapping['TEMP_CTD_RBR_MEAN'] == 'sea_water_temperature'
        
        assert 'SAL_RBR_MEAN' in result.canonical_mapping
        assert result.canonical_mapping['SAL_RBR_MEAN'] == 'sea_water_practical_salinity'
        
        assert 'BARO_PRES_MEAN' in result.canonical_mapping
        assert result.canonical_mapping['BARO_PRES_MEAN'] == 'air_pressure'
        
        assert 'WSPD_MEAN' in result.canonical_mapping
        assert result.canonical_mapping['WSPD_MEAN'] == 'wind_speed'
        
        # Check CF mappings
        assert 'TEMP_CTD_RBR_MEAN' in result.cf_mapping
        assert result.cf_mapping['TEMP_CTD_RBR_MEAN']['cf_standard_name'] == 'sea_water_temperature'
        assert result.cf_mapping['TEMP_CTD_RBR_MEAN']['confidence'] == 1.0  # Exact match via alias
        
        print("✓ Saildrone columns mapped correctly to CF standard names")

    def test_custom_table_overrides_builtin(self, tmp_path):
        """Test that custom tables override built-in tables."""
        # Create custom CF table with only one term
        custom_cf = tmp_path / "custom-cf.json"
        custom_cf.write_text('["custom_variable"]')
        
        # Create custom alias table
        custom_alias = tmp_path / "custom-alias.json"
        custom_alias.write_text('{"custom_variable": ["CUSTOM_COL"]}')
        
        config = SemanticConfig(
            enabled=True,
            cf_table_path=str(custom_cf),
            alias_table_path=str(custom_alias),
            min_confidence=0.7
        )
        
        mapper = SemanticMapper(config)
        
        # Should only have custom tables, not built-in
        assert len(mapper._cf_table) == 1
        assert "custom_variable" in mapper._cf_table
        assert "sea_water_temperature" not in mapper._cf_table
        
        assert len(mapper._aliases) == 1
        assert "custom_col" in mapper._aliases
        assert "temp_ctd_rbr_mean" not in mapper._aliases
        
        print("✓ Custom tables correctly override built-in tables")

    def test_builtin_cf_coverage(self):
        """Test that built-in CF table has good oceanographic coverage."""
        cf_path = Path(__file__).parent.parent.parent / "semantic" / "data" / "cf-standard-names.json"
        with open(cf_path) as f:
            cf_data = json.load(f)
        
        # Check for key oceanographic categories
        ocean_vars = [v for v in cf_data if v.startswith("sea_water_")]
        assert len(ocean_vars) >= 10, f"Should have at least 10 sea_water_ variables, got {len(ocean_vars)}"
        
        air_vars = [v for v in cf_data if v.startswith("air_")]
        assert len(air_vars) >= 3, f"Should have at least 3 air_ variables, got {len(air_vars)}"
        
        wind_vars = [v for v in cf_data if "wind" in v]
        assert len(wind_vars) >= 5, f"Should have at least 5 wind variables, got {len(wind_vars)}"
        
        wave_vars = [v for v in cf_data if "wave" in v]
        assert len(wave_vars) >= 5, f"Should have at least 5 wave variables, got {len(wave_vars)}"
        
        platform_vars = [v for v in cf_data if v.startswith("platform_")]
        assert len(platform_vars) >= 3, f"Should have at least 3 platform variables, got {len(platform_vars)}"
        
        print(f"✓ CF table coverage: {len(ocean_vars)} ocean, {len(air_vars)} air, "
              f"{len(wind_vars)} wind, {len(wave_vars)} wave, {len(platform_vars)} platform")

    def test_builtin_alias_coverage(self):
        """Test that built-in alias table covers common Saildrone sensors."""
        alias_path = Path(__file__).parent.parent.parent / "semantic" / "data" / "saildrone-aliases.json"
        with open(alias_path) as f:
            alias_data = json.load(f)
        
        # Count total aliases
        total_aliases = sum(len(v) if isinstance(v, list) else 1 for v in alias_data.values())
        assert total_aliases >= 50, f"Should have at least 50 aliases, got {total_aliases}"
        
        # Check key Saildrone sensors are covered
        required_sensors = {
            "sea_water_temperature": ["TEMP_CTD_RBR_MEAN"],
            "sea_water_practical_salinity": ["SAL_RBR_MEAN"],
            "air_temperature": ["TEMP_AIR_MEAN"],
            "air_pressure": ["BARO_PRES_MEAN"],
            "wind_speed": ["WSPD_MEAN"],
            "mass_concentration_of_oxygen_in_sea_water": ["O2_CONC_RBR_MEAN"],
            "mass_concentration_of_chlorophyll_a_in_sea_water": ["CHLA_WETLABS_MEAN"]
        }
        
        for canonical, required_alias in required_sensors.items():
            assert canonical in alias_data, f"Missing canonical name: {canonical}"
            aliases = alias_data[canonical]
            assert isinstance(aliases, list), f"{canonical} should map to list of aliases"
            for alias in required_alias:
                assert alias in aliases, f"{canonical} should include alias {alias}"
        
        print(f"✓ Alias table has {total_aliases} total aliases covering {len(alias_data)} canonical names")
