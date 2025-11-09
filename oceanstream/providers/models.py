from __future__ import annotations
from pydantic import BaseModel
from typing import Optional, Dict, List

class OceanographicMeasurement(BaseModel):
    platform_id: str
    latitude: float
    longitude: float
    timestamp: str
    temperature: Optional[float] = None
    salinity: Optional[float] = None
    depth: Optional[float] = None
    other_measurements: Optional[Dict] = None

class GeoParquetData(BaseModel):
    measurements: List[OceanographicMeasurement]
    latitude_bins: List[float]
    longitude_bins: List[float]
