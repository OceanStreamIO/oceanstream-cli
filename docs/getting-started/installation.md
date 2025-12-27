# Installation

This guide walks you through installing OceanStream and its dependencies.

## System Requirements

### Minimum Requirements

- **Python**: 3.11 or higher (3.12+ recommended)
- **Operating System**: macOS, Linux, or Windows
- **Disk Space**: 500 MB for base installation
- **Memory**: 4 GB RAM minimum (8 GB+ recommended for large datasets)

### Recommended Setup

- **Python**: 3.12
- **OS**: macOS or Linux (better performance for parallel processing)
- **Disk Space**: 10 GB+ for processing large datasets
- **Memory**: 16 GB RAM for production workloads

## Installation Methods

OceanStream is organized into modular components. Install only what you need based on your data processing requirements.

### 1. Quick Install (Recommended)

Install OceanStream with geotrack processing capabilities:

```bash
# Using pip
pip install oceanstream

# Or using pipx for isolated installation
pipx install oceanstream
```

### 2. Install from Source (Development)

For the latest features or contributing to development:

```bash
# Clone the repository
git clone https://github.com/OceanStreamIO/oceanstream-newcli.git
cd oceanstream-newcli

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in editable mode with geotrack support
pip install -e ".[geotrack]"
```

### 3. Install with Specific Modules

OceanStream supports different data types through optional modules:

```bash
# Core + geotrack (GPS/navigation data → GeoParquet)
pip install oceanstream[geotrack]

# Core + echodata (echosounder data → Zarr)
pip install oceanstream[echodata]

# All processing modules
pip install oceanstream[all]
```

**Available modules:**
- `geotrack` - GPS/navigation track processing (pandas, geopandas, shapely)
- `echodata` - Echosounder data processing (echopype, xarray, zarr) - *Coming soon*
- `multibeam` - Multibeam sonar processing - *Planned*
- `adcp` - ADCP current profiler processing - *Planned*
- `all` - All processing modules

### 4. Development Installation

For contributing to OceanStream:

```bash
# Clone repository
git clone https://github.com/OceanStreamIO/oceanstream-newcli.git
cd oceanstream-newcli

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install all modules + development tools
pip install -e ".[all]"
pip install -r requirements-dev.txt

# Verify installation
make test
```

## Optional Dependencies

### GIS Visualization (PMTiles)

For generating PMTiles vector tiles for web visualization:

**Install GDAL with Parquet support:**

```bash
# macOS (via Homebrew)
brew install gdal

# Ubuntu/Debian
sudo apt-get install gdal-bin

# Verify GDAL supports Parquet
ogrinfo --formats | grep -i parquet
```

**Install PMTiles CLI:**

```bash
# Using npm
npm install -g @maplibre/pmtiles

# Or using Go
go install github.com/protomaps/go-pmtiles/cmd/pmtiles@latest

# Verify installation
pmtiles --version
```

**Note**: PMTiles generation is optional. OceanStream will automatically skip it if the tools aren't available.

### Azure Storage (Optional)

For uploading data to Azure Blob Storage:

```bash
# Azure CLI (optional, for manual operations)
# macOS
brew install azure-cli

# Ubuntu/Debian
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Windows
# Download installer from https://aka.ms/installazurecliwindows
```

The Azure Python SDK is included with OceanStream - no additional installation needed.

## Verify Installation

### Check OceanStream Version

```bash
oceanstream --version
```

Expected output:
```
oceanstream version 0.10.0
```

### Verify Python Environment

```bash
python --version
```

Should show Python 3.11 or higher.

### Test Basic Functionality

```bash
# Check available commands
oceanstream --help

# List data providers
oceanstream providers

# Check configuration
oceanstream configure storage --show
```

### Run Test Data Processing (Optional)

If you installed from source, test with sample data:

```bash
# Process test fixtures
oceanstream process geotrack \
  --input-source oceanstream/tests/data/raw_data \
  --output-dir ./test-output \
  --verbose

# Check output
ls -R ./test-output
```

## Platform-Specific Instructions

### macOS

#### Using Homebrew

```bash
# Install Python 3.12
brew install python@3.12

# Install OceanStream
pip3.12 install oceanstream

# Optional: Install GDAL for PMTiles
brew install gdal
```

#### Using pyenv

```bash
# Install pyenv
brew install pyenv

# Install Python 3.12
pyenv install 3.12.0
pyenv global 3.12.0

# Install OceanStream
pip install oceanstream
```

### Linux (Ubuntu/Debian)

```bash
# Update package list
sudo apt-get update

# Install Python 3.12
sudo apt-get install python3.12 python3.12-venv python3-pip

# Install OceanStream
pip3 install oceanstream

# Optional: Install GDAL
sudo apt-get install gdal-bin python3-gdal
```

### Linux (RHEL/CentOS/Rocky)

```bash
# Enable EPEL repository
sudo dnf install epel-release

# Install Python 3.12
sudo dnf install python3.12 python3.12-pip

# Install OceanStream
pip3.12 install oceanstream

# Optional: Install GDAL
sudo dnf install gdal gdal-python3
```

### Windows

#### Using Python Installer

1. Download Python 3.12 from [python.org](https://www.python.org/downloads/)
2. Run installer, check "Add Python to PATH"
3. Open Command Prompt or PowerShell:

```powershell
# Verify Python installation
python --version

# Install OceanStream
pip install oceanstream

# Verify installation
oceanstream --version
```

#### Using WSL2 (Recommended for Advanced Features)

```bash
# Install WSL2 with Ubuntu
wsl --install

# Inside WSL2, follow Linux instructions
sudo apt-get update
sudo apt-get install python3.12 python3.12-venv
pip3 install oceanstream
```

## Virtual Environments

We strongly recommend using virtual environments to avoid dependency conflicts.

### Using venv (Built-in)

```bash
# Create virtual environment
python3.12 -m venv oceanstream-env

# Activate (Linux/macOS)
source oceanstream-env/bin/activate

# Activate (Windows)
oceanstream-env\Scripts\activate

# Install OceanStream
pip install oceanstream

# Deactivate when done
deactivate
```

### Using conda

```bash
# Create conda environment
conda create -n oceanstream python=3.12

# Activate environment
conda activate oceanstream

# Install OceanStream
pip install oceanstream
```

### Using pipx (Isolated Installation)

```bash
# Install pipx
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# Install OceanStream in isolated environment
pipx install oceanstream

# OceanStream is now available globally
oceanstream --version
```

## Upgrading

### Upgrade to Latest Version

```bash
# Using pip
pip install --upgrade oceanstream

# Using pipx
pipx upgrade oceanstream
```

### Upgrade from Source

```bash
cd oceanstream-newcli
git pull origin main
pip install -e ".[all]" --upgrade
```

## Troubleshooting

### "Command not found: oceanstream"

**Problem**: Python scripts directory not in PATH.

**Solutions**:

1. **Add Python scripts to PATH** (Linux/macOS):
   ```bash
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc
   ```

2. **Use full path**:
   ```bash
   python -m oceanstream --help
   ```

3. **Use pipx** (recommended):
   ```bash
   pipx install oceanstream
   ```

### "ImportError: No module named 'geopandas'"

**Problem**: Missing optional dependencies.

**Solution**:
```bash
pip install oceanstream[geotrack]
```

### "Python version not supported"

**Problem**: Python version is too old.

**Solution**:
```bash
# Check current version
python --version

# Install Python 3.12
# macOS
brew install python@3.12

# Ubuntu
sudo apt-get install python3.12
```

### GDAL Installation Issues

**Problem**: GDAL installation fails or missing Parquet driver.

**Solutions**:

1. **macOS**: Use Homebrew
   ```bash
   brew install gdal
   ```

2. **Linux**: Use package manager
   ```bash
   sudo apt-get install gdal-bin libgdal-dev
   ```

3. **Windows**: Use OSGeo4W or conda
   ```bash
   conda install -c conda-forge gdal
   ```

4. **Verify Parquet support**:
   ```bash
   ogrinfo --formats | grep -i parquet
   ```

### Permission Errors on Windows

**Problem**: "Access denied" when installing.

**Solution**: Run as administrator or use `--user` flag:
```powershell
pip install --user oceanstream
```

## Next Steps

Now that OceanStream is installed:

1. **[Quick Start](quick-start.md)** - Process your first dataset in 5 minutes
2. **[Configuration](configuration.md)** - Set up storage providers and preferences
3. **CLI Reference** - Explore all available commands (see `oceanstream --help`)

## Getting Help

- **Documentation**: [https://oceanstream.io/docs](https://oceanstream.io/docs)
- **GitHub Issues**: [https://github.com/OceanStreamIO/oceanstream-newcli/issues](https://github.com/OceanStreamIO/oceanstream-newcli/issues)
- **Community**: Join our discussions on GitHub

---

**Installation complete!** Proceed to the [Quick Start](quick-start.md) guide to process your first dataset.
