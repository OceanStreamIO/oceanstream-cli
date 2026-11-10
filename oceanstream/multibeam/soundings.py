"""Multibeam soundings: the per-sounding batch type and its georeferencing."""

from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


@dataclass
class SoundingBatch:
    """Valid bottom detections of a run of pings, one array entry per sounding.

    The reference position and heading are those of the vessel reference point at the
    ping. ``along_m`` and ``across_m`` are the sounding's distances from that point in
    the heading frame (forward, starboard), and ``depth_below_tx_m`` its depth below
    the transmit transducer, as Kongsberg's XYZ88 datagram gives them.
    ``transducer_depth_m`` is the transmit transducer's depth below the water line at
    the ping, so depth below the water line is the sum of the two.
    """

    ping_time: np.ndarray  # datetime64[ns]
    beam: np.ndarray  # uint16
    ref_latitude: np.ndarray  # degrees
    ref_longitude: np.ndarray  # degrees
    heading_deg: np.ndarray  # degrees clockwise from true north
    along_m: np.ndarray
    across_m: np.ndarray
    depth_below_tx_m: np.ndarray
    transducer_depth_m: np.ndarray
    backscatter_db: np.ndarray
    beam_angle_deg: np.ndarray  # signed, from the vertical; port negative
    quality_factor: np.ndarray  # uint8

    def __len__(self) -> int:
        return int(self.ping_time.size)

    def select(self, mask: np.ndarray) -> SoundingBatch:
        """Return the soundings where ``mask`` is true."""
        return SoundingBatch(**{f.name: getattr(self, f.name)[mask] for f in fields(self)})

    @staticmethod
    def concat(batches: list[SoundingBatch]) -> SoundingBatch:
        """Concatenate batches in order."""
        return SoundingBatch(
            **{
                f.name: np.concatenate([getattr(b, f.name) for b in batches])
                for f in fields(SoundingBatch)
            }
        )


def offset_latlon(
    latitude: np.ndarray, longitude: np.ndarray, north_m: np.ndarray, east_m: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Move points by local north and east offsets in metres on the WGS84 ellipsoid.

    Uses the meridional and prime-vertical radii of curvature at each point, which is
    exact to millimetres over the tens to hundreds of metres of a multibeam swath.
    """
    phi = np.radians(np.asarray(latitude, dtype=np.float64))
    s = np.sin(phi)
    w = np.sqrt(1.0 - WGS84_E2 * s * s)
    meridional = WGS84_A * (1.0 - WGS84_E2) / w**3
    prime_vertical = WGS84_A / w
    lat = latitude + np.degrees(np.asarray(north_m, dtype=np.float64) / meridional)
    lon = longitude + np.degrees(
        np.asarray(east_m, dtype=np.float64) / (prime_vertical * np.cos(phi))
    )
    return lat, lon


def heading_frame_to_north_east(
    heading_deg: np.ndarray, along_m: np.ndarray, across_m: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Rotate forward/starboard offsets into north/east offsets for a true heading."""
    h = np.radians(np.asarray(heading_deg, dtype=np.float64))
    along = np.asarray(along_m, dtype=np.float64)
    across = np.asarray(across_m, dtype=np.float64)
    north = along * np.cos(h) - across * np.sin(h)
    east = along * np.sin(h) + across * np.cos(h)
    return north, east


def georeference(batch: SoundingBatch) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Latitude, longitude and depth below the water line of every sounding."""
    north, east = heading_frame_to_north_east(batch.heading_deg, batch.along_m, batch.across_m)
    lat, lon = offset_latlon(batch.ref_latitude, batch.ref_longitude, north, east)
    depth = batch.depth_below_tx_m.astype(np.float64) + batch.transducer_depth_m
    return lat, lon, depth


def utm_epsg(latitude: float, longitude: float) -> int:
    """EPSG code of the WGS84 UTM zone containing a point (no Norway/Svalbard exceptions)."""
    zone = int(np.floor((longitude + 180.0) / 6.0)) % 60 + 1
    return (32600 if latitude >= 0 else 32700) + zone
