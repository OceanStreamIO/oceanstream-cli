import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Lazy-loaded config instance
_config_instance = None

def _reset_config_instance():
    """Reset the config instance (useful for testing)."""
    global _config_instance
    _config_instance = None

def _get_metadata_dir() -> Path:
    """Get metadata directory from config or environment.
    
    Priority:
    1. OCEANSTREAM_METADATA_DIR environment variable
    2. paths.metadata_dir from oceanstream.toml
    3. Default: ~/.oceanstream/metadata
    """
    global _config_instance
    
    # Env var takes precedence
    if env_val := os.getenv("OCEANSTREAM_METADATA_DIR"):
        return Path(env_val).expanduser().resolve()
    
    # Then config file
    if _config_instance is None:
        from oceanstream.configuration import get_config
        _config_instance = get_config()
    
    metadata_dir = _config_instance.metadata_dir
    
    # Append /metadata to the base directory for backwards compatibility
    if metadata_dir.name != "metadata":
        metadata_dir = metadata_dir / "metadata"
    
    return metadata_dir


def _get_semantic_enable() -> bool:
    """Get semantic enable flag from config or environment.
    
    Priority:
    1. SEMANTIC_ENABLE environment variable
    2. semantic.enable from oceanstream.toml
    3. Default: False
    """
    global _config_instance
    
    # Env var takes precedence
    if env_val := os.getenv("SEMANTIC_ENABLE"):
        return env_val.lower() in {"1", "true", "yes"}
    
    # Then config file
    if _config_instance is None:
        from oceanstream.configuration import get_config
        _config_instance = get_config()
    
    return bool(_config_instance.get("semantic.enable", False))


def _get_semantic_generate_stac() -> bool:
    """Get semantic STAC generation flag from config or environment.
    
    Priority:
    1. SEMANTIC_GENERATE_STAC environment variable
    2. semantic.generate_stac from oceanstream.toml
    3. Default: True
    """
    global _config_instance
    
    # Env var takes precedence
    if env_val := os.getenv("SEMANTIC_GENERATE_STAC"):
        return env_val.lower() in {"1", "true", "yes"}
    
    # Then config file
    if _config_instance is None:
        from oceanstream.configuration import get_config
        _config_instance = get_config()
    
    return bool(_config_instance.get("semantic.generate_stac", True))


def _get_semantic_min_confidence() -> float:
    """Get semantic minimum confidence from config or environment.
    
    Priority:
    1. SEMANTIC_MIN_CONFIDENCE environment variable
    2. semantic.min_confidence from oceanstream.toml
    3. Default: 0.7
    """
    global _config_instance
    
    # Env var takes precedence
    if env_val := os.getenv("SEMANTIC_MIN_CONFIDENCE"):
        return float(env_val)
    
    # Then config file
    if _config_instance is None:
        from oceanstream.configuration import get_config
        _config_instance = get_config()
    
    value = _config_instance.get("semantic.min_confidence", 0.7)
    return float(value)


def _get_semantic_cf_table() -> str:
    """Get semantic CF table path from config or environment.
    
    Priority:
    1. SEMANTIC_CF_TABLE environment variable
    2. semantic.cf_table from oceanstream.toml
    3. Default: ""
    """
    global _config_instance
    
    # Env var takes precedence
    if env_val := os.getenv("SEMANTIC_CF_TABLE"):
        return env_val
    
    # Then config file
    if _config_instance is None:
        from oceanstream.configuration import get_config
        _config_instance = get_config()
    
    return str(_config_instance.get("semantic.cf_table", ""))


def _get_semantic_alias_table() -> str:
    """Get semantic alias table path from config or environment.
    
    Priority:
    1. SEMANTIC_ALIAS_TABLE environment variable
    2. semantic.alias_table from oceanstream.toml
    3. Default: ""
    """
    global _config_instance
    
    # Env var takes precedence
    if env_val := os.getenv("SEMANTIC_ALIAS_TABLE"):
        return env_val
    
    # Then config file
    if _config_instance is None:
        from oceanstream.configuration import get_config
        _config_instance = get_config()
    
    return str(_config_instance.get("semantic.alias_table", ""))


class Settings:
    """Global settings for OceanStream.
    
    Settings are loaded in the following order (later overrides earlier):
    1. Built-in defaults
    2. oceanstream.toml configuration file
    3. Environment variables
    
    The configuration file can use environment variable substitution.
    """
    
    # Metadata directory for tracking processed files and campaigns
    # Default: ~/.oceanstream/metadata
    # Config key: paths.metadata_dir
    # Env var: OCEANSTREAM_METADATA_DIR
    METADATA_DIR = _get_metadata_dir()
    
    # Azure Storage settings
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME")
    
    # Legacy path settings (kept for backwards compatibility)
    RAW_DATA_PATH = os.getenv("RAW_DATA_PATH", "data/raw_data")
    OUTPUT_PATH = os.getenv("OUTPUT_PATH", "data/output")
    
    # Semantic mapping configuration
    # Config keys: semantic.enable, semantic.generate_stac, semantic.min_confidence,
    #              semantic.cf_table, semantic.alias_table
    # Env vars: SEMANTIC_ENABLE, SEMANTIC_GENERATE_STAC, SEMANTIC_MIN_CONFIDENCE,
    #           SEMANTIC_CF_TABLE, SEMANTIC_ALIAS_TABLE
    SEMANTIC_ENABLE = _get_semantic_enable()
    SEMANTIC_GENERATE_STAC = _get_semantic_generate_stac()
    SEMANTIC_MIN_CONFIDENCE = _get_semantic_min_confidence()
    SEMANTIC_CF_TABLE = _get_semantic_cf_table()
    SEMANTIC_ALIAS_TABLE = _get_semantic_alias_table()


