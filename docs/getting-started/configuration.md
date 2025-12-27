# Configuration

Learn how to configure OceanStream for your environment and workflows.

## Overview

OceanStream supports flexible configuration through:

1. **TOML configuration files** - Persistent settings
2. **Environment variables** - Dynamic overrides
3. **Command-line flags** - Per-run options

Settings are resolved in order of precedence: **CLI flags > Environment variables > Config file > Defaults**

## Quick Configuration

### Storage Providers

Configure where processed data is uploaded:

```bash
# Azure Blob Storage (you'll be prompted for credentials)
oceanstream configure storage \
  --provider azure \
  --container-name oceanstream-data

# Local filesystem
oceanstream configure storage \
  --provider local \
  --base-path /mnt/storage

# Set active provider
oceanstream configure storage --set-active azure

# View configuration
oceanstream configure storage --show
```

### Campaign Settings

The easiest way to configure campaigns is through the TOML config file (see below), but you can also use environment variables:

```bash
# Override metadata directory (where campaign registrations are stored)
export OCEANSTREAM_METADATA_DIR=~/.oceanstream/metadata

# Note: Use TOML config file for output directory configuration
# See Configuration Files section below
```

## Configuration Files

### Creating a Config File

OceanStream uses TOML format, matching Python's `pyproject.toml` standard.

**Create `oceanstream.toml` in your project directory:**

```toml
# oceanstream.toml

[paths]
metadata_dir = "~/.oceanstream"
output_dir = "./output"

[campaigns]
auto_register = true

[processing]
verbose = false
force_reprocess = false
```

### Config File Locations

OceanStream searches for configuration in this order:

1. `--config-file` CLI argument (explicit path)
2. `./oceanstream.toml` (current directory)
3. `~/.oceanstream/oceanstream.toml` (user home)
4. Built-in defaults

**Specify custom config:**
```bash
oceanstream --config-file /path/to/config.toml process geotrack ...
```

### Configuration Sections

#### `[paths]` - File System Locations

```toml
[paths]
# Where campaign metadata is stored
metadata_dir = "~/.oceanstream/metadata"

# Default output directory for processed data
output_dir = "./output"
```

- **metadata_dir**: Campaign registrations and processing history
  - Default: `~/.oceanstream/metadata`
  - Supports `~` expansion
  
- **output_dir**: Where GeoParquet files are written
  - Default: `./output`
  - Can be overridden per-run with `--output-dir`

#### `[campaigns]` - Campaign Management

```toml
[campaigns]
# Automatically register campaigns after processing
auto_register = true
```

- **auto_register**: Register campaigns in metadata directory
  - Default: `true`
  - Set to `false` to skip registration

#### `[processing]` - Default Processing Behavior

```toml
[processing]
# Show detailed processing information
verbose = false

# Force reprocessing of existing campaigns
force_reprocess = false

# Skip actual processing (preview mode)
dry_run = false
```

These can be overridden with CLI flags:
- `--verbose` / `-v`
- `--force-reprocess`
- `--dry-run`

## Environment Variables

Environment variables provide dynamic configuration and can substitute into TOML files.

### Standard Environment Variables

```bash
# Metadata directory (campaign registrations and tracking)
export OCEANSTREAM_METADATA_DIR=~/.oceanstream/metadata

# Azure storage credentials (legacy - use 'configure storage' instead)
export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;..."
export AZURE_CONTAINER_NAME="oceanstream-data"

# Legacy path settings (for backwards compatibility)
export OUTPUT_PATH=./processed-data
export RAW_DATA_PATH=./raw-data
```

**Note**: Modern OceanStream uses TOML configuration files for most settings. Environment variables are primarily for:
- Overriding metadata directory location
- Providing credentials (though `configure storage` is preferred)
- Environment-specific configuration (dev/staging/prod)

### Variable Substitution in TOML

Reference environment variables in your config file:

**Required variable (error if not set):**
```toml
[storage]
connection_string = "${AZURE_STORAGE_CONNECTION_STRING}"
```

**Variable with default value:**
```toml
[storage]
account_name = "${AZURE_STORAGE_ACCOUNT:-mydefaultaccount}"
container_name = "${CONTAINER_NAME:-oceanstream-data}"
```

**Mixed content:**
```toml
[paths]
output_dir = "./output/${ENVIRONMENT:-dev}"
metadata_dir = "~/.oceanstream/${ENVIRONMENT}"
```

**Example usage:**
```bash
# Development
export ENVIRONMENT=dev
oceanstream process geotrack ...
# Uses: ./output/dev and ~/.oceanstream/dev

# Production
export ENVIRONMENT=prod
oceanstream process geotrack ...
# Uses: ./output/prod and ~/.oceanstream/prod
```

## Storage Provider Configuration

Storage providers are configured separately and stored encrypted at `~/.oceanstream/storage.json`.

### Configure Azure Storage

```bash
# Interactive configuration (prompts for credentials)
oceanstream configure storage \
  --provider azure \
  --container-name oceanstream-data

# You'll be prompted to enter:
# - Connection string (securely, won't echo to terminal)

# Verify configuration
oceanstream configure storage --show
```

### Configure Local Storage

```bash
oceanstream configure storage \
  --provider local \
  --base-path /mnt/oceanstream-data
```

### Configure S3 (Coming Soon)

```bash
oceanstream configure storage \
  --provider s3 \
  --bucket-name oceanstream-data \
  --region us-west-2
```

### Manage Storage Providers

```bash
# List all configured providers
oceanstream configure storage --list

# Show detailed configuration
oceanstream configure storage --show

# Set active provider
oceanstream configure storage --set-active azure

# Remove a provider
oceanstream configure storage --remove azure-staging
```

See [Cloud Storage](../guide/features/cloud-storage.md) for complete details.

## Configuration Examples

### Development Setup

**dev.toml:**
```toml
[paths]
metadata_dir = "./dev-metadata"
output_dir = "./dev-output"

[processing]
verbose = true  # Always show detailed logs

[campaigns]
auto_register = false  # Don't clutter metadata
```

**Usage:**
```bash
oceanstream --config-file dev.toml process geotrack \
  --input-source ./test-data \
  --campaign-id test
```

### Production Setup

**prod.toml:**
```toml
[paths]
metadata_dir = "/var/lib/oceanstream/metadata"
output_dir = "/data/oceanstream/output"

[processing]
verbose = false
force_reprocess = false

[campaigns]
auto_register = true

[storage]
# Use environment variables for credentials
connection_string = "${AZURE_STORAGE_CONNECTION_STRING}"
container_name = "oceanstream-prod"
```

**Setup:**
```bash
# Set production credentials
export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;..."

# Configure storage
oceanstream configure storage \
  --provider azure \
  --container-name oceanstream-prod

# Process with upload
oceanstream --config-file prod.toml process geotrack \
  --input-source /data/raw \
  --upload
```

### Multi-Environment Setup

**oceanstream.toml:**
```toml
[paths]
metadata_dir = "~/.oceanstream/${ENVIRONMENT:-dev}"
output_dir = "./output/${ENVIRONMENT:-dev}"

[processing]
verbose = "${VERBOSE:-false}"

[storage]
container_name = "oceanstream-${ENVIRONMENT:-dev}"
connection_string = "${AZURE_STORAGE_CONNECTION_STRING}"
```

**Usage:**
```bash
# Development
export ENVIRONMENT=dev
export VERBOSE=true
oceanstream process geotrack --input-source ./data

# Staging
export ENVIRONMENT=staging
export VERBOSE=false
oceanstream process geotrack --input-source ./data

# Production
export ENVIRONMENT=prod
export AZURE_STORAGE_CONNECTION_STRING="..."
oceanstream process geotrack --input-source ./data --upload
```

### Per-Campaign Configuration

Different campaigns can use different settings:

```bash
# Research campaign - local storage, verbose
oceanstream --config-file research.toml process geotrack \
  --input-source ./research-data \
  --campaign-id research_2024 \
  --verbose

# Production campaign - cloud upload, quiet
oceanstream --config-file prod.toml process geotrack \
  --input-source ./prod-data \
  --campaign-id mission_2024 \
  --upload
```

## Security Best Practices

### Credentials Storage

✅ **Recommended:**
```bash
# Use configure storage for encrypted credential storage
oceanstream configure storage --provider azure --container-name data
# Prompts for connection string securely
```

❌ **Avoid:**
```toml
# Don't hardcode credentials in config files!
[storage]
connection_string = "DefaultEndpointsProtocol=https;AccountKey=SECRET..."
```

### Config File Permissions

Protect your configuration files:

```bash
# Secure configuration file
chmod 600 oceanstream.toml

# Secure storage configuration
chmod 600 ~/.oceanstream/storage.json
chmod 600 ~/.oceanstream/.storage_key
```

### Environment Variables

```bash
# Load from secure .env file (never commit!)
source .env

# Or use a secrets manager
export AZURE_STORAGE_CONNECTION_STRING=$(vault read secret/azure-conn)
```

## Command-Line Overrides

All configuration can be overridden per-run:

```bash
# Override output directory
oceanstream process geotrack \
  --output-dir /tmp/test-output \
  --input-source ./data

# Override campaign settings
oceanstream process geotrack \
  --input-source ./data \
  --campaign-id override_campaign \
  --force-reprocess \
  --verbose

# Override storage
oceanstream configure storage --set-active local
oceanstream process geotrack --input-source ./data --upload
```

## Troubleshooting

### Configuration File Not Found

**Error:** `ConfigurationError: Configuration file not found: oceanstream.toml`

**Solutions:**
1. Create `oceanstream.toml` in current directory
2. Specify path: `--config-file /path/to/config.toml`
3. Use built-in defaults (no config file needed)

### Environment Variable Not Set

**Error:** `ConfigurationError: Environment variable 'AZURE_STORAGE_CONNECTION_STRING' is not set`

**Solutions:**
1. Set the variable:
   ```bash
   export AZURE_STORAGE_CONNECTION_STRING="..."
   ```

2. Provide default in config:
   ```toml
   connection_string = "${AZURE_STORAGE_CONNECTION_STRING:-}"
   ```

3. Use configure storage instead:
   ```bash
   oceanstream configure storage --provider azure ...
   ```

### Permission Denied

**Error:** `PermissionError: [Errno 13] Permission denied: '~/.oceanstream/storage.json'`

**Solution:**
```bash
# Fix permissions
chmod 600 ~/.oceanstream/storage.json
chmod 700 ~/.oceanstream/
```

### Config Not Loading

**Verification steps:**

1. **Check file exists:**
   ```bash
   ls -la oceanstream.toml
   ```

2. **Validate TOML syntax:**
   ```bash
   python -c "import tomllib; tomllib.load(open('oceanstream.toml', 'rb'))"
   ```

3. **Check current directory:**
   ```bash
   pwd  # Should be where oceanstream.toml exists
   ```

4. **Use explicit path:**
   ```bash
   oceanstream --config-file $(pwd)/oceanstream.toml process geotrack ...
   ```

## Python API

Access configuration programmatically:

```python
from oceanstream.config import Settings

# Get settings instance
settings = Settings()

# Access values
print(settings.METADATA_DIR)
print(settings.OUTPUT_PATH)

# Check if storage is configured
from oceanstream.storage.manager import load_storage_configuration

config = load_storage_configuration()
if config.active_provider:
    print(f"Active provider: {config.active_provider}")
```

## Next Steps

Now that you understand configuration:

1. **[Cloud Storage](../guide/features/cloud-storage.md)** - Set up storage providers
2. **CLI Reference** - All CLI options (see `oceanstream --help`)
3. **Python API** - Programmatic configuration

---

**Configuration complete!** Your OceanStream setup is now customized for your workflow.
