"""Oceanstream package

Process oceanographic measurements into partitioned GeoParquet datasets.

SDK Usage:
    # Quick start
    from oceanstream import get_provider, convert
    
    provider = get_provider('saildrone')
    convert(provider, input_source='./data', output_dir='./output', campaign_id='my_campaign')
    
    # Or use the processor class directly
    from oceanstream import GeotrackProcessor
    
    processor = GeotrackProcessor(provider=provider, campaign_id='my_campaign')
    csv_files = processor.scan_input_source(Path('./data'))
    df = processor.process_files(csv_files)
"""

__version__ = "0.1.0"

# Lazy imports for SDK convenience
# These are loaded on first access to avoid import overhead


def __getattr__(name: str):
    """Lazy import SDK modules for convenience access."""
    # Provider functions
    if name == "get_provider":
        from .providers import get_provider
        return get_provider
    if name == "list_providers":
        from .providers import list_providers
        return list_providers
    if name == "ProviderBase":
        from .providers import ProviderBase
        return ProviderBase
    
    # Geotrack functions
    if name == "convert":
        from .geotrack import convert
        return convert
    if name == "process":
        from .geotrack import process
        return process
    if name == "generate_tiles":
        from .geotrack import generate_tiles
        return generate_tiles
    if name == "generate_report":
        from .geotrack import generate_report
        return generate_report
    if name == "GeotrackProcessor":
        from .geotrack.processor import GeotrackProcessor
        return GeotrackProcessor
    
    # Sensor classes
    if name == "Sensor":
        from .sensors import Sensor
        return Sensor
    if name == "SensorCatalogue":
        from .sensors import SensorCatalogue
        return SensorCatalogue
    if name == "get_sensor_catalogue":
        from .sensors import get_sensor_catalogue
        return get_sensor_catalogue
    
    # Semantic mapping
    if name == "SemanticMapper":
        from .semantic import SemanticMapper
        return SemanticMapper
    if name == "SemanticConfig":
        from .semantic import SemanticConfig
        return SemanticConfig
    
    # STAC
    if name == "emit_stac_collection_and_item":
        from .stac import emit_stac_collection_and_item
        return emit_stac_collection_and_item
    
    # Settings
    if name == "Settings":
        from .config.settings import Settings
        return Settings
    
    raise AttributeError(f"module 'oceanstream' has no attribute '{name}'")


__all__ = [
    "__version__",
    # Providers
    "get_provider",
    "list_providers",
    "ProviderBase",
    # Geotrack
    "convert",
    "process",
    "generate_tiles",
    "generate_report",
    "GeotrackProcessor",
    # Sensors
    "Sensor",
    "SensorCatalogue",
    "get_sensor_catalogue",
    # Semantic
    "SemanticMapper",
    "SemanticConfig",
    # STAC
    "emit_stac_collection_and_item",
    # Config
    "Settings",
]
