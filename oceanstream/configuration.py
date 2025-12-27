"""Configuration management for OceanStream.

Supports TOML configuration files with environment variable substitution.
Configuration lookup order:
1. --config-file CLI argument (if provided)
2. ./oceanstream.toml (current directory)
3. Built-in defaults
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any


class ConfigurationError(Exception):
    """Raised when configuration is invalid or cannot be loaded."""
    pass


def _substitute_env_vars(value: Any) -> Any:
    """Recursively substitute environment variables in configuration values.
    
    Supports syntax:
    - ${VAR_NAME} - standard substitution
    - ${VAR_NAME:-default} - with default value
    - $VAR_NAME - simple substitution
    
    Args:
        value: Configuration value (can be str, dict, list, or primitive)
        
    Returns:
        Value with environment variables substituted
        
    Examples:
        "azure://${AZURE_ACCOUNT}" -> "azure://myaccount"
        "${HOME}/data" -> "/Users/john/data"
        "${MISSING:-default}" -> "default"
    """
    if isinstance(value, str):
        # Pattern: ${VAR_NAME} or ${VAR_NAME:-default} or $VAR_NAME
        def replacer(match):
            var_expr = match.group(1) or match.group(2)  # ${...} or $...
            
            # Check for default value syntax: ${VAR:-default}
            if ':-' in var_expr:
                var_name, default = var_expr.split(':-', 1)
                return os.environ.get(var_name, default)
            else:
                var_name = var_expr
                env_value = os.environ.get(var_name)
                if env_value is None:
                    raise ConfigurationError(
                        f"Environment variable '{var_name}' is not set and no default provided"
                    )
                return env_value
        
        # Match ${VAR} or ${VAR:-default} or $VAR
        pattern = r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)'
        return re.sub(pattern, replacer, value)
    
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    
    else:
        return value


class OceanStreamConfig:
    """OceanStream configuration manager.
    
    Loads configuration from TOML files and provides access to settings
    with environment variable substitution.
    """
    
    DEFAULT_CONFIG = {
        "paths": {
            "metadata_dir": "~/.oceanstream",
            "output_dir": "./output",
        },
        "campaigns": {
            "auto_register": True,
        },
        "processing": {
            "verbose": False,
            "force_reprocess": False,
        },
        "storage": {
            # Example: "connection_string": "${AZURE_STORAGE_CONNECTION_STRING}"
        },
        "semantic": {
            "enable": False,
            "generate_stac": True,
            "min_confidence": 0.7,
            "cf_table": "",
            "alias_table": "",
        },
    }
    
    def __init__(self, config_file: Path | str | None = None):
        """Initialize configuration.
        
        Args:
            config_file: Path to configuration file. If None, looks for
                        ./oceanstream.toml in current directory.
        """
        self._config = self.DEFAULT_CONFIG.copy()
        self._config_file: Path | None = None
        
        # Load configuration
        if config_file:
            # User-provided config file
            config_path = Path(config_file)
            if not config_path.exists():
                raise ConfigurationError(f"Configuration file not found: {config_file}")
            self._load_config(config_path)
        else:
            # Look for ./oceanstream.toml
            default_path = Path.cwd() / "oceanstream.toml"
            if default_path.exists():
                self._load_config(default_path)
    
    def _load_config(self, config_path: Path) -> None:
        """Load configuration from TOML file.
        
        Args:
            config_path: Path to TOML configuration file
            
        Raises:
            ConfigurationError: If file cannot be loaded or parsed
        """
        try:
            with open(config_path, "rb") as f:
                loaded_config = tomllib.load(f)
            
            # Deep merge with defaults
            self._deep_merge(self._config, loaded_config)
            self._config_file = config_path
            
        except tomllib.TOMLDecodeError as e:
            raise ConfigurationError(f"Invalid TOML syntax in {config_path}: {e}")
        except Exception as e:
            raise ConfigurationError(f"Error loading configuration from {config_path}: {e}")
    
    def _deep_merge(self, base: dict, update: dict) -> None:
        """Deep merge update dict into base dict (in-place).
        
        Args:
            base: Base dictionary to update
            update: Dictionary with updates
        """
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def get(self, key: str, default: Any = None, substitute_env: bool = True) -> Any:
        """Get configuration value by dot-separated key.
        
        Args:
            key: Configuration key (e.g., "paths.metadata_dir")
            default: Default value if key not found
            substitute_env: If True, substitute environment variables
            
        Returns:
            Configuration value
            
        Examples:
            config.get("paths.metadata_dir")
            config.get("storage.connection_string")
        """
        parts = key.split(".")
        value = self._config
        
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        
        if substitute_env:
            try:
                value = _substitute_env_vars(value)
            except ConfigurationError:
                # If env var substitution fails, return default
                return default
        
        return value
    
    def get_path(self, key: str, default: str | None = None) -> Path:
        """Get configuration value as a Path with expansion.
        
        Args:
            key: Configuration key
            default: Default value if key not found
            
        Returns:
            Expanded Path object
        """
        value = self.get(key, default)
        if value is None:
            raise ConfigurationError(f"Configuration key '{key}' not found and no default provided")
        return Path(value).expanduser().resolve()
    
    @property
    def config_file(self) -> Path | None:
        """Return the path to the loaded configuration file, if any."""
        return self._config_file
    
    @property
    def metadata_dir(self) -> Path:
        """Get metadata directory path."""
        return self.get_path("paths.metadata_dir", "~/.oceanstream")
    
    @property
    def output_dir(self) -> Path:
        """Get default output directory path."""
        return self.get_path("paths.output_dir", "./output")
    
    @property
    def auto_register_campaigns(self) -> bool:
        """Check if campaigns should be auto-registered."""
        return bool(self.get("campaigns.auto_register", True))
    
    def to_dict(self) -> dict:
        """Return configuration as dictionary (with env vars substituted)."""
        return _substitute_env_vars(self._config.copy())


# Global config instance
_global_config: OceanStreamConfig | None = None


def get_config(config_file: Path | str | None = None) -> OceanStreamConfig:
    """Get or create global configuration instance.
    
    Args:
        config_file: Optional configuration file path
        
    Returns:
        Global OceanStreamConfig instance
    """
    global _global_config
    
    if _global_config is None or config_file is not None:
        _global_config = OceanStreamConfig(config_file)
    
    return _global_config


def reset_config() -> None:
    """Reset global configuration (useful for testing)."""
    global _global_config
    _global_config = None
