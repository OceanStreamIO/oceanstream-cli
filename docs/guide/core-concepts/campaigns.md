# Campaign Management

Campaigns are the primary organizational unit in OceanStream. A campaign represents a distinct data collection effort - such as a research cruise, autonomous vehicle deployment, or field study - and serves as a container for all related data and metadata.

## What is a Campaign?

A **campaign** is a logical grouping of oceanographic data with:

- **Unique identifier**: Campaign ID (e.g., `FK161229`, `SD1030_2023`)
- **Metadata**: Platform, temporal, spatial, and provenance information
- **Data organization**: All processed data stored under campaign ID
- **STAC integration**: Campaign metadata flows into STAC collections
- **Persistent storage**: Metadata stored separately from data files

**Key principle**: Create campaign once, process data many times.

## Campaign Directory Structure

OceanStream uses two separate directory structures for campaigns:

### 1. Campaign Registry (~/.oceanstream/campaigns/)

Persistent campaign metadata stored in your home directory:

```
~/.oceanstream/
└── campaigns/
    ├── FK161229/
    │   └── campaign.json        # Campaign metadata
    ├── SD1030_2023/
    │   └── campaign.json
    └── SD1033_2023/
        └── campaign.json
```

**Purpose**: 
- Survives deletion of output data
- Centralized registry of all campaigns
- Enables campaign listing and search

### 2. Campaign Output (user-specified output directory)

Processed data stored in user-specified location:

```
output/
└── FK161229/                    # Campaign ID
    ├── lat_bin=-43/lon_bin=-170/
    │   └── *.parquet            # GeoParquet data
    ├── stac/
    │   ├── collection.json      # STAC collection
    │   └── items/
    │       └── *.json           # STAC items
    └── tiles/
        └── track.pmtiles        # Optional PMTiles
```

**Purpose**:
- User can delete and recreate data
- Campaign metadata persists in registry
- Enables data reprocessing

## Campaign Lifecycle

### 1. Create Campaign

Register a new campaign with metadata:

```bash
# Minimal (only ID required)
oceanstream campaign create SD1030_2023

# With platform and attribution
oceanstream campaign create SD1030_2023 \
  --platform-id "sd1030" \
  --attribution "Saildrone Inc."

# Full research cruise metadata
oceanstream campaign create FK161229 \
  --platform-id "R/V Falkor" \
  --platform-name "Research Vessel Falkor" \
  --platform-type "Research Vessel" \
  --description "Hydrothermal vent ecosystem study" \
  --start-date 2016-12-29 \
  --end-date 2017-01-20 \
  --bbox "-180,-50,180,50" \
  --attribution "Schmidt Ocean Institute" \
  --license "CC-BY-4.0" \
  --doi "10.5281/zenodo.123456" \
  --chief-scientist "Dr. Jane Smith" \
  --institution "Schmidt Ocean Institute" \
  --keywords "oceanography,hydrothermal,vents"
```

**Output**:
```
[campaign create] ✓ Campaign created successfully
[campaign create]   Campaign ID: FK161229
[campaign create]   Registry: ~/.oceanstream/campaigns/FK161229
[campaign create]   Metadata: ~/.oceanstream/campaigns/FK161229/campaign.json

[campaign create] You can now process data for this campaign:
  oceanstream process geotrack --campaign-id FK161229 --input-source <data>
```

### 2. List Campaigns

View all registered campaigns:

```bash
oceanstream campaign list
```

**Output**:
```
Campaign ID       Platform          Created               Status
───────────────────────────────────────────────────────────────────
FK161229         R/V Falkor        2024-12-01 10:30:00   Active
SD1030_2023      sd1030           2024-11-15 08:45:00   Active
SD1033_2023      sd1033           2024-11-20 14:20:00   Active

Total: 3 campaigns
```

### 3. Show Campaign Details

Display full campaign metadata:

```bash
oceanstream campaign show FK161229
```

**Output**:
```json
{
  "campaign_id": "FK161229",
  "created_at": "2024-12-01T10:30:00Z",
  "updated_at": "2024-12-01T10:30:00Z",
  "oceanstream_version": "0.1.0",
  "platform_id": "R/V Falkor",
  "platform_name": "Research Vessel Falkor",
  "platform_type": "Research Vessel",
  "description": "Hydrothermal vent ecosystem study",
  "start_date": "2016-12-29",
  "end_date": "2017-01-20",
  "bbox": [-180.0, -50.0, 180.0, 50.0],
  "attribution": "Schmidt Ocean Institute",
  "license": "CC-BY-4.0",
  "doi": "10.5281/zenodo.123456",
  "keywords": ["oceanography", "hydrothermal", "vents"],
  "chief_scientist": "Dr. Jane Smith",
  "institution": "Schmidt Ocean Institute"
}
```

### 4. Update Campaign Metadata

Modify existing campaign metadata:

```bash
oceanstream campaign update FK161229 \
  --end-date 2017-01-25 \
  --description "Updated description"
```

**Note**: Updates automatically set `updated_at` timestamp.

### 5. Process Data

Process data for a campaign (uses metadata from registry):

```bash
# First data batch
oceanstream process geotrack \
  --campaign-id FK161229 \
  --input-source ./nav_data/ \
  --output-dir ./output

# Additional data (appends to existing)
oceanstream process geotrack \
  --campaign-id FK161229 \
  --input-source ./sensor_data/ \
  --output-dir ./output
```

Campaign metadata automatically:
- ✅ Used in STAC collection generation
- ✅ Validated against data extents
- ✅ Included in output summaries

### 6. Inspect Campaign Data

Check what data has been processed:

```bash
oceanstream campaign inspect FK161229 --output-dir ./output
```

**Output**:
```
Campaign: FK161229
Registry: ~/.oceanstream/campaigns/FK161229
Output: ./output/FK161229

Data Products:
  ✓ GeoParquet: 1,234,567 rows (12 columns, 45.2 MB)
  ✓ STAC Collection: ./output/FK161229/stac/collection.json
  ✓ STAC Items: 3 items
  ✓ PMTiles: ./output/FK161229/tiles/track.pmtiles (5.2 MB)

Sample Data (first 5 rows):
  time                    latitude   longitude  TEMP_AIR_MEAN  WIND_SPEED_MEAN
  2016-12-29T00:00:00Z   -43.2156   -170.4321  18.5           5.2
  2016-12-29T00:01:00Z   -43.2157   -170.4322  18.4           5.3
  ...
```

### 7. Delete Campaign

Remove campaign from registry:

```bash
# Delete metadata only (data preserved)
oceanstream campaign delete FK161229

# Delete metadata AND data
oceanstream campaign delete FK161229 --delete-data --output-dir ./output
```

**Warning**: Deleting campaign metadata removes it from registry. You'll need to recreate the campaign to process more data.

## Campaign Metadata

### Required Fields

Only **campaign_id** is required when creating a campaign:

```bash
oceanstream campaign create MY_CAMPAIGN
```

All other fields are optional and can be added later with `campaign update`.

### Optional Fields

#### Platform Metadata
- `--platform-id`: Platform identifier (e.g., `sd1030`, `R/V Falkor`)
- `--platform-name`: Full platform name (e.g., `Research Vessel Falkor`)
- `--platform-type`: Platform type (e.g., `Saildrone`, `Research Vessel`, `Glider`)

#### Temporal Metadata
- `--start-date`: Campaign start date (ISO 8601: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`)
- `--end-date`: Campaign end date (ISO 8601)

#### Spatial Metadata
- `--bbox`: Bounding box as `minlon,minlat,maxlon,maxlat` (e.g., `-180,-50,180,50`)

#### Provenance Metadata
- `--attribution`: Data attribution/citation (e.g., `"Schmidt Ocean Institute"`)
- `--license`: Data license (e.g., `CC-BY-4.0`, `MIT`, `proprietary`)
- `--doi`: Dataset DOI (e.g., `10.5281/zenodo.123456`)
- `--source-repository`: Source repository URL (e.g., `https://doi.org/10.1234/repo`)

#### Descriptive Metadata
- `--description`: Campaign description
- `--keywords`: Comma-separated keywords (e.g., `oceanography,hydrothermal,vents`)

#### Scientific Metadata
- `--chief-scientist`: Chief scientist name
- `--institution`: Institution name
- `--project`: Project name
- `--funding`: Funding information

### Metadata Validation

OceanStream validates metadata to prevent errors:

**Date validation**:
```bash
oceanstream campaign create TEST --start-date "2023-13-45"
# ERROR: Invalid start_date format. Use ISO 8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
```

**Bounding box validation**:
```bash
# Out of range
oceanstream campaign create TEST --bbox "-200,-90,180,90"
# ERROR: Longitude values must be in range [-180, 180], got: -200.0

# Wrong order
oceanstream campaign create TEST --bbox "180,-90,-180,90"
# ERROR: minlon (180.0) must be less than maxlon (-180.0)

# Latitude out of range
oceanstream campaign create TEST --bbox "-180,-100,180,90"
# ERROR: Latitude values must be in range [-90, 90], got: -100.0
```

**Duplicate campaign**:
```bash
oceanstream campaign create FK161229  # Second attempt
# ERROR: Campaign 'FK161229' already exists at ~/.oceanstream/campaigns/FK161229
```

## Working with Campaigns

### Pre-Registration Workflow

Create campaign before data arrives:

```bash
# 1. Register campaign with known metadata
oceanstream campaign create ARCTIC_2024 \
  --platform-id "R/V Sikuliaq" \
  --start-date 2024-08-01 \
  --bbox "-180,60,180,90" \
  --attribution "University of Alaska" \
  --license "CC-BY-4.0"

# 2. Process data as it arrives
oceanstream process geotrack \
  --campaign-id ARCTIC_2024 \
  --input-source ./incoming/batch1/

# 3. Append more data later
oceanstream process geotrack \
  --campaign-id ARCTIC_2024 \
  --input-source ./incoming/batch2/
```

### Incremental Processing

Process data in batches (automatic deduplication):

```bash
# Day 1: Process initial data
oceanstream process geotrack \
  --campaign-id SD1030_2023 \
  --input-source ./day1_data/ \
  --output-dir ./output

# Day 2: Append new data
oceanstream process geotrack \
  --campaign-id SD1030_2023 \
  --input-source ./day2_data/ \
  --output-dir ./output

# Day 3: Append more data
oceanstream process geotrack \
  --campaign-id SD1030_2023 \
  --input-source ./day3_data/ \
  --output-dir ./output
```

**Automatic features**:
- ✅ Detects duplicate files (SHA256 hash)
- ✅ Prevents reprocessing same data
- ✅ Deduplicates rows by primary keys (time, lat, lon, trajectory)
- ✅ Updates STAC metadata incrementally
- ✅ Preserves existing data

### Reprocessing Data

If you need to start fresh:

```bash
# Option 1: Delete output data, keep campaign metadata
rm -rf ./output/FK161229
oceanstream process geotrack \
  --campaign-id FK161229 \
  --input-source ./data/ \
  --output-dir ./output

# Option 2: Force reprocess (clears file tracking)
oceanstream process geotrack \
  --campaign-id FK161229 \
  --input-source ./data/ \
  --output-dir ./output \
  --force-reprocess
```

### Multi-Campaign Projects

Process multiple campaigns in same output directory:

```bash
# Campaign 1
oceanstream campaign create SD1030_2023 --platform-id "sd1030"
oceanstream process geotrack --campaign-id SD1030_2023 --input-source ./sd1030/

# Campaign 2
oceanstream campaign create SD1033_2023 --platform-id "sd1033"
oceanstream process geotrack --campaign-id SD1033_2023 --input-source ./sd1033/

# Campaign 3
oceanstream campaign create SD1079_2023 --platform-id "sd1079"
oceanstream process geotrack --campaign-id SD1079_2023 --input-source ./sd1079/
```

**Output structure**:
```
output/
├── SD1030_2023/
│   ├── lat_bin=X/lon_bin=Y/*.parquet
│   └── stac/
├── SD1033_2023/
│   ├── lat_bin=X/lon_bin=Y/*.parquet
│   └── stac/
└── SD1079_2023/
    ├── lat_bin=X/lon_bin=Y/*.parquet
    └── stac/
```

## Campaign Best Practices

### Naming Conventions

Use consistent, descriptive campaign IDs:

**Research vessels**:
- Format: `{Vessel}_{Cruise}` (e.g., `FK161229`, `AT4201`)
- Include year if helpful: `FALKOR_2016_229`

**Autonomous platforms**:
- Format: `{Platform}_{Location}_{Year}` (e.g., `SD1030_TPOS_2023`)
- Include deployment ID: `GLIDER_123_BERING_2024`

**Field studies**:
- Format: `{Project}_{Site}_{Year}` (e.g., `ARGO_PACIFIC_2024`)

**Avoid**:
- Spaces (use underscores or hyphens)
- Special characters (except `-`, `_`)
- Very generic names (`TEST`, `DATA`)

### Metadata Completeness

**Minimum recommended metadata**:
```bash
oceanstream campaign create CAMPAIGN_ID \
  --platform-id "platform" \
  --attribution "Organization Name" \
  --start-date YYYY-MM-DD
```

**Full metadata for publication**:
```bash
oceanstream campaign create CAMPAIGN_ID \
  --platform-id "platform" \
  --platform-name "Full Platform Name" \
  --platform-type "Platform Type" \
  --attribution "Organization Name" \
  --license "CC-BY-4.0" \
  --doi "10.xxxx/xxxxx" \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --bbox "minlon,minlat,maxlon,maxlat" \
  --description "Detailed description" \
  --keywords "keyword1,keyword2,keyword3" \
  --chief-scientist "Name" \
  --institution "Institution Name"
```

### Data Organization

**One campaign per deployment**:
- Separate deployments = separate campaigns
- Even if same platform (e.g., `SD1030_2023`, `SD1030_2024`)

**Group related data**:
- All data from same deployment in one campaign
- Navigation, sensors, CTD, etc. all use same campaign_id
- Enables unified STAC collection

**Use appropriate output directories**:
- Research projects: `./output/` (simple)
- Operational: `/data/oceanstream/` (centralized)
- Multi-project: `/data/{project}/oceanstream/` (organized)

### STAC Integration

Campaign metadata automatically flows into STAC collections:

**Campaign metadata → STAC collection**:
- `platform_id` → `platform`
- `platform_name` → `platform:name`
- `attribution` → `providers` (host/processor)
- `license` → `license`
- `doi` → `sci:doi`
- `keywords` → `keywords`
- `bbox` → `extent.spatial.bbox`
- `start_date/end_date` → `extent.temporal.interval`

**Benefits**:
- Consistent metadata across data products
- STAC-compliant collections
- Discoverable via STAC catalogs
- Integration with STAC tools

## Troubleshooting

### Campaign Already Exists

**Problem**:
```
ERROR: Campaign 'FK161229' already exists at ~/.oceanstream/campaigns/FK161229
```

**Solutions**:

**Option 1: Use different campaign ID**:
```bash
oceanstream campaign create FK161229_REPROCESSED
```

**Option 2: Update existing campaign**:
```bash
oceanstream campaign update FK161229 --description "New description"
```

**Option 3: Delete and recreate**:
```bash
oceanstream campaign delete FK161229
oceanstream campaign create FK161229 --platform-id "R/V Falkor"
```

### No Processed Data Found

**Problem**:
```bash
oceanstream campaign inspect FK161229 --output-dir ./output
# ERROR: No processed data found for campaign 'FK161229' in ./output
```

**Solutions**:

**Check campaign registry**:
```bash
oceanstream campaign show FK161229
# Verify campaign exists in registry
```

**Process data**:
```bash
oceanstream process geotrack \
  --campaign-id FK161229 \
  --input-source ./data/ \
  --output-dir ./output
```

**Check output directory**:
```bash
ls -la ./output/FK161229/
# Verify directory exists and has data
```

### Duplicate Data Processing

**Problem**: Same data processed multiple times, creating duplicates.

**Solution**: OceanStream automatically prevents this with file tracking:

```bash
# First run: Processes all files
oceanstream process geotrack --campaign-id TEST --input-source ./data/

# Second run: Detects files already processed
oceanstream process geotrack --campaign-id TEST --input-source ./data/
# WARNING: File 'file.csv' was already processed (SHA256: abc123...)
# Skipping to prevent duplicates.
```

**Force reprocessing** (if needed):
```bash
oceanstream process geotrack \
  --campaign-id TEST \
  --input-source ./data/ \
  --force-reprocess
```

### Invalid Metadata

**Problem**: Metadata validation fails.

**Common issues**:

**Invalid date format**:
```bash
# Wrong
--start-date "12/29/2016"

# Correct
--start-date "2016-12-29"
# or
--start-date "2016-12-29T00:00:00Z"
```

**Invalid bbox format**:
```bash
# Wrong (wrong separator)
--bbox "-180 -50 180 50"

# Correct
--bbox "-180,-50,180,50"
```

**Out of range coordinates**:
```bash
# Wrong
--bbox "-200,-100,200,100"

# Correct (lon: -180 to 180, lat: -90 to 90)
--bbox "-180,-90,180,90"
```

## Python API

### Create Campaign

```python
from oceanstream.geotrack.campaign import create_campaign
from pathlib import Path

# Minimal
campaign_dir = create_campaign(
    campaign_id="FK161229",
    metadata={},
)

# With metadata
campaign_dir = create_campaign(
    campaign_id="FK161229",
    metadata={
        "platform_id": "R/V Falkor",
        "platform_name": "Research Vessel Falkor",
        "attribution": "Schmidt Ocean Institute",
        "start_date": "2016-12-29",
        "end_date": "2017-01-20",
        "bbox": [-180, -50, 180, 50],
        "license": "CC-BY-4.0",
    },
    verbose=True,
)

print(f"Campaign created at: {campaign_dir}")
```

### Load Campaign Metadata

```python
from oceanstream.geotrack.campaign import load_campaign_metadata

metadata = load_campaign_metadata("FK161229")
if metadata:
    print(f"Platform: {metadata.get('platform_id')}")
    print(f"Start: {metadata.get('start_date')}")
else:
    print("Campaign not found")
```

### Update Campaign Metadata

```python
from oceanstream.geotrack.campaign import update_campaign_metadata

update_campaign_metadata(
    campaign_id="FK161229",
    updates={
        "end_date": "2017-01-25",
        "description": "Updated description",
    }
)
```

### List All Campaigns

```python
from oceanstream.geotrack.campaign import list_campaigns

campaigns = list_campaigns()
for campaign in campaigns:
    print(f"{campaign['campaign_id']}: {campaign.get('platform_id')}")
```

### Delete Campaign

```python
from oceanstream.geotrack.campaign import delete_campaign

delete_campaign("FK161229", verbose=True)
```

### Inspect Campaign Data

```python
from oceanstream.geotrack.campaign import inspect_campaign_data
from pathlib import Path

info = inspect_campaign_data(
    campaign_id="FK161229",
    output_dir=Path("./output"),
    limit=10,  # Number of sample rows
    verbose=True,
)

print(f"Has GeoParquet: {info['has_geoparquet']}")
print(f"Total rows: {info['geoparquet_info']['total_rows']:,}")
print(f"STAC items: {len(info['stac_items'])}")

# Access sample data
if info['geoparquet_sample'] is not None:
    print(info['geoparquet_sample'].head())
```

## Next Steps

- [Geotrack Processing](geotrack-convert-overview.md) - Process data for campaigns
- [STAC Metadata](../features/stac-metadata.md) - STAC collection generation
<!-- TODO: Add append-update.md guide -->
- **Append/Update** - Incremental data processing
- [CLI Reference](geotrack-convert-reference.md) - Complete command reference
