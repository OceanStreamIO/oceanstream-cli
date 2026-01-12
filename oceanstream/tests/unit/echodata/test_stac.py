"""Tests for echodata STAC module.

Tests the STAC metadata generation for echodata products:
- MVBS Zarr datasets
- NASC Zarr datasets
- Echogram PNG images with segment coordinates
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest


class TestGetEchodataSummaries:
    """Tests for get_echodata_summaries function."""
    
    def test_get_echodata_summaries_basic(self):
        """Test generating echodata summaries for STAC collection."""
        from oceanstream.echodata.stac import get_echodata_summaries
        
        summaries = get_echodata_summaries(
            sonar_model="EK80",
            frequencies_khz=[38, 200],
            waveform_mode="CW",
            denoising_methods=["background", "transient", "impulse", "attenuation"],
        )
        
        assert summaries["sonar_model"] == "EK80"
        assert summaries["frequencies_khz"] == [38, 200]
        assert "mvbs" in summaries["products"]
        assert "nasc" in summaries["products"]
        assert len(summaries["denoising_applied"]) == 4
    
    def test_get_echodata_summaries_defaults(self):
        """Test summaries with default values."""
        from oceanstream.echodata.stac import get_echodata_summaries
        
        summaries = get_echodata_summaries()
        
        assert summaries["sonar_model"] == "EK80"
        assert summaries["waveform_mode"] == "CW"
        assert "mvbs" in summaries["products"]


class TestEmitEchodataItem:
    """Tests for emit_echodata_item function."""
    
    def test_emit_echodata_item_mvbs(self, tmp_path):
        """Test emitting STAC item for MVBS product."""
        from oceanstream.echodata.stac import emit_echodata_item
        
        # Create mock echodata directory with MVBS zarr
        echodata_dir = tmp_path / "echodata"
        mvbs_dir = echodata_dir / "mvbs"
        mvbs_dir.mkdir(parents=True)
        mvbs_zarr = mvbs_dir / "mvbs.zarr"
        mvbs_zarr.mkdir()
        (mvbs_zarr / ".zarray").write_text("{}")
        (mvbs_zarr / ".zattrs").write_text("{}")
        
        item = emit_echodata_item(
            echodata_dir=echodata_dir,
            campaign_id="TPOS2023",
            product="mvbs",
            sonar_model="EK80",
            frequencies_khz=[38, 200],
        )
        
        assert item is not None
        assert item["type"] == "Feature"
        assert item["stac_version"] == "1.0.0"
        assert "mvbs" in item["id"]
        assert item["properties"]["echodata:product"] == "mvbs"
        assert item["properties"]["echodata:frequencies_khz"] == [38, 200]
        assert "zarr" in item["assets"]
    
    def test_emit_echodata_item_nasc(self, tmp_path):
        """Test emitting STAC item for NASC product."""
        from oceanstream.echodata.stac import emit_echodata_item
        
        # Create mock echodata directory with NASC zarr
        echodata_dir = tmp_path / "echodata"
        nasc_dir = echodata_dir / "nasc"
        nasc_dir.mkdir(parents=True)
        nasc_zarr = nasc_dir / "nasc.zarr"
        nasc_zarr.mkdir()
        (nasc_zarr / ".zarray").write_text("{}")
        
        item = emit_echodata_item(
            echodata_dir=echodata_dir,
            campaign_id="TPOS2023",
            product="nasc",
            sonar_model="EK80",
            frequencies_khz=[38, 200],
            range_bin="10m",
            dist_bin="0.5nmi",
        )
        
        assert item is not None
        assert item["properties"]["echodata:product"] == "nasc"
    
    def test_emit_echodata_item_missing_product(self, tmp_path):
        """Test that missing product returns None."""
        from oceanstream.echodata.stac import emit_echodata_item
        
        echodata_dir = tmp_path / "echodata"
        echodata_dir.mkdir(parents=True)
        
        item = emit_echodata_item(
            echodata_dir=echodata_dir,
            campaign_id="TPOS2023",
            product="mvbs",
        )
        
        assert item is None


class TestEmitEchodataCollection:
    """Tests for emit_echodata_collection function."""
    
    def test_emit_echodata_collection_standalone(self, tmp_path):
        """Test creating standalone echodata STAC collection."""
        from oceanstream.echodata.stac import emit_echodata_collection
        
        # Create echodata directory with products
        echodata_dir = tmp_path / "echodata"
        (echodata_dir / "mvbs").mkdir(parents=True)
        mvbs_zarr = echodata_dir / "mvbs" / "mvbs.zarr"
        mvbs_zarr.mkdir()
        (mvbs_zarr / ".zarray").write_text("{}")
        
        collection_path = emit_echodata_collection(
            campaign_dir=tmp_path,
            campaign_id="ACOUSTIC_ONLY",
            sonar_model="EK80",
            frequencies_khz=[38, 200],
        )
        
        assert collection_path.exists()
        
        with open(collection_path) as f:
            collection = json.load(f)
        
        assert collection["type"] == "Collection"
        assert collection["id"] == "ACOUSTIC_ONLY"
        assert "echodata" in collection["summaries"]
        assert collection["summaries"]["echodata"]["sonar_model"] == "EK80"


class TestAddEchodataToCollection:
    """Tests for extending geotrack collection with echodata."""
    
    def test_add_echodata_to_existing_collection(self, tmp_path):
        """Test adding echodata to existing geotrack collection."""
        from oceanstream.echodata.stac import add_echodata_to_collection
        
        # Create mock existing collection
        existing_collection = {
            "type": "Collection",
            "stac_version": "1.0.0",
            "id": "TPOS2023",
            "description": "TPOS 2023 Campaign",
            "extent": {
                "spatial": {"bbox": [[-140.5, 7.0, -139.5, 8.0]]},
                "temporal": {"interval": [["2023-06-01T00:00:00Z", "2023-10-07T23:59:59Z"]]},
            },
            "summaries": {
                "platforms": [{"id": "sd1033", "type": "Saildrone Explorer"}],
            },
            "assets": {
                "pmtiles": {"href": "../tiles/track.pmtiles"},
            },
            "links": [],
        }
        
        # Write collection
        stac_dir = tmp_path / "stac"
        stac_dir.mkdir()
        collection_path = stac_dir / "collection.json"
        collection_path.write_text(json.dumps(existing_collection))
        
        # Create mock echodata directories with actual zarr stores
        echodata_dir = tmp_path / "echodata"
        mvbs_zarr = echodata_dir / "mvbs" / "mvbs.zarr"
        mvbs_zarr.mkdir(parents=True)
        (mvbs_zarr / ".zarray").write_text("{}")
        
        nasc_zarr = echodata_dir / "nasc" / "nasc.zarr"
        nasc_zarr.mkdir(parents=True)
        (nasc_zarr / ".zarray").write_text("{}")
        
        # Add echodata (API uses include_mvbs, include_nasc, not products)
        updated_path = add_echodata_to_collection(
            collection_path=collection_path,
            echodata_dir=echodata_dir,
            sonar_model="EK80",
            frequencies_khz=[38, 200],
            include_mvbs=True,
            include_nasc=True,
        )
        
        with open(updated_path) as f:
            updated = json.load(f)
        
        assert "echodata" in updated["summaries"]
        assert "mvbs" in updated["assets"]
        assert "nasc" in updated["assets"]
        assert updated["summaries"]["echodata"]["sonar_model"] == "EK80"


class TestCreateSegmentsGeojson:
    """Tests for segment GeoJSON generation."""
    
    def test_extract_segment_coordinates_from_ds(self, tmp_path):
        """Test extracting coordinates from dataset."""
        from oceanstream.echodata.stac.segments import extract_segment_coordinates
        import numpy as np
        import xarray as xr
        
        # Create mock xarray dataset with position data
        n_pings = 100
        base_time = np.datetime64("2023-06-01T00:00:00")
        ping_time = base_time + np.arange(n_pings) * np.timedelta64(1, "m")
        
        ds = xr.Dataset(
            {
                "Sv": (["channel", "ping_time", "depth"], np.random.rand(2, n_pings, 50)),
                "latitude": (["ping_time"], np.linspace(7.0, 8.0, n_pings)),
                "longitude": (["ping_time"], np.linspace(-140.0, -139.0, n_pings)),
            },
            coords={
                "channel": ["38kHz", "200kHz"],
                "ping_time": ping_time,
                "depth": np.linspace(0, 200, 50),
            },
        )
        
        # Save as zarr
        zarr_path = tmp_path / "sv.zarr"
        ds.to_zarr(zarr_path)
        
        # Extract segments
        segments = extract_segment_coordinates(zarr_path, segment_by="daily")
        
        assert len(segments) >= 1
        assert "start_lat" in segments[0]
        assert "end_lat" in segments[0]


class TestStacImports:
    """Test that all STAC functions are importable."""
    
    def test_imports_from_stac_module(self):
        """Test imports from oceanstream.echodata.stac."""
        from oceanstream.echodata.stac import (
            emit_stac,
            emit_echodata_collection,
            emit_echodata_item,
            add_echodata_to_collection,
            get_echodata_summaries,
            create_segments_geojson,
            create_echogram_segment,
            extract_segment_coordinates,
        )
        
        assert callable(emit_stac)
        assert callable(emit_echodata_collection)
        assert callable(emit_echodata_item)
        assert callable(add_echodata_to_collection)
        assert callable(get_echodata_summaries)
        assert callable(create_segments_geojson)
        assert callable(create_echogram_segment)
        assert callable(extract_segment_coordinates)
    
    def test_imports_from_main_echodata_module(self):
        """Test imports from oceanstream.echodata."""
        from oceanstream.echodata import (
            emit_stac,
            create_segments_geojson,
        )
        
        assert callable(emit_stac)
        assert callable(create_segments_geojson)
