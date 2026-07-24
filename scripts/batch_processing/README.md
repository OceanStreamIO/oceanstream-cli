# Saildrone TPOS 2023 — Batch Processing Pipeline

Standalone Dask-based pipeline for processing echosounder data from the Saildrone TPOS 2023 campaign. Uses the **oceanstream** library (no Prefect) with a distributed Dask cluster.

## Pipeline stages

```
Sv Zarr (Azure "processed" container)
  → GPS merge (per file)
  → Day-level concatenation (short_pulse / long_pulse categories)
  → Denoise (impulse, attenuated, transient, background)
  → Seabed mask
  → MVBS + NASC computation
  → Echogram generation (source, denoised, MVBS)
  → PMTiles (NASC track) + COG heatmaps (MVBS raster)
  → Campaign-wide MVBS Zarr (all days concatenated)
```

## Prerequisites

```bash
# Activate the Python 3.11 environment with oceanstream[echodata]
source /path/to/venv/bin/activate

# Install oceanstream with echodata extras
pip install -e "/path/to/sd-data-ingest[echodata]"

# Additional dependencies
pip install dask[distributed] adlfs azure-storage-blob python-dotenv rasterio
```

Required environment variables (or `.env` file):
```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_CONTAINER_NAME=oceanstream-data
PROCESSED_CONTAINER_NAME=processed
```

## Quick start

### Local test (2-3 days of data)
```bash
python process_campaign.py --local-test
# or with custom date range:
python process_campaign.py --local-test --start-date 2023-06-22 --end-date 2023-06-24
```

### Local with full options
```bash
python process_campaign.py \
  --start-date 2023-06-22 \
  --end-date 2023-07-01 \
  --n-workers 8 \
  --memory-limit 16GB \
  --gps-data-file gps_data.json \
  --denoise-config denoise.toml \
  --mvbs-range-bin 20m \
  --mvbs-ping-time-bin 5s \
  --nasc-range-bin 10m \
  --nasc-dist-bin 0.5nmi
```

### Full campaign on Azure VM
```bash
# 1. Provision VM
python infra.py create --vm-size Standard_E16s_v5

# 2. SSH into VM, cd to workspace, then:
python process_campaign.py \
  --from-env \
  --auto-deallocate \
  --n-workers 8 \
  --memory-limit 14GB \
  --build-campaign-zarr
```

### Skip specific stages
```bash
python process_campaign.py \
  --start-date 2023-06-22 \
  --end-date 2023-06-24 \
  --skip-echograms \
  --skip-pmtiles \
  --skip-nasc
```

## GPS data export

The pipeline reads GPS location data from a pre-exported JSON file (to avoid database dependency at runtime). Run this once:

```bash
# Export all GPS data for the campaign
python export_gps.py --cruise-id SD_TPOS2023 --output gps_data.json

# Export a date range
python export_gps.py --cruise-id SD_TPOS2023 --output gps_data.json \
  --start 2023-06-22 --end 2023-08-01
```

If no GPS file is provided, the script discovers files from the Azure container directly (without GPS merge).

## Denoise configuration

Create a TOML file for denoise parameter tuning:

```bash
python process_campaign.py --denoise-config denoise.toml
```

See `denoise_example.toml` for the parameter format.

## Azure VM management

```bash
python infra.py create            # Create processing VM
python infra.py status            # Check VM status
python infra.py deallocate        # Deallocate (stop billing)
python infra.py delete            # Delete VM entirely
```

### Recommended VM sizes

| Mode | VM Size | vCPU | RAM | Use case |
|------|---------|------|-----|----------|
| Local test | N/A | - | - | 2-3 days, LocalCluster |
| Standard | Standard_E16s_v5 | 16 | 128 GB | Full campaign |
| Large | Standard_E32s_v5 | 32 | 256 GB | + campaign Sv concat |

## Output structure

```
{output_container}/
  {cruise_id}/
    {file_name}/{file_name}.zarr          # Per-file Sv with GPS
    days/{YYYY-MM-DD}/
      short_pulse.zarr                     # Day concatenated Sv
      short_pulse_denoised.zarr            # Denoised Sv
      short_pulse_masked.zarr              # Seabed-masked Sv
      short_pulse_mvbs.zarr                # MVBS
      short_pulse_nasc.zarr                # NASC
      long_pulse.zarr
      ...
    echograms/{YYYY-MM-DD}/
      *_source.png
      *_denoised.png
      *_mvbs.png
    campaign_mvbs.zarr                     # All days MVBS concatenated
    campaign_sv.zarr                       # (optional) All days Sv
```

## Files

| File | Purpose |
|------|---------|
| `process_campaign.py` | Main pipeline orchestrator |
| `config.py` | Configuration dataclasses |
| `export_gps.py` | GPS data export from PostgresDB |
| `infra.py` | Azure VM provisioning/deallocation |
| `denoise_example.toml` | Example denoise configuration |
