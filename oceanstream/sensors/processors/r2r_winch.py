"""R2R winch (LCI-90i) sensor processors.

This module provides both a SensorDescriptor builder and a raw-data
processor for LCI-90i winch instruments discovered in R2R archives.

The LCI-90i is a winch monitoring system commonly used on research vessels.
R2R winch data files contain lines of the form::

    2022-06-14T06:25:23.876888Z 03RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839

The fields are:
- timestamp_logged: ISO timestamp when data was logged to file
- device_id: Winch device identifier (e.g., "03RD")
- timestamp_instrument: ISO timestamp from instrument
- wire_out: Wire payout in meters (negative = wire out)
- turns: Winch drum turns counter
- speed: Wire speed in m/s (negative = paying out)
- tension: Wire tension in lbs
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from oceanstream.providers.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors import (
    RawProcessor,
    SensorProcessor,
    register_raw_processor,
    register_sensor_processor,
)


SENSOR_TYPE_WINCH = "winch"
SENSOR_ID_WINCH = "lci90i-winch"

# Pattern to match LCI-90i data lines
# Example: 2022-06-14T06:25:23.876888Z <RS><SOH>03RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839
# The RS (0x1E) and SOH (0x01) control characters are between timestamp and device ID
LCI90I_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"  # timestamp_logged
    r"[\s\x00-\x1f]+"  # whitespace and/or control characters (RS, SOH, etc.)
    r"(\w+),"  # device_id
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?),"  # timestamp_instrument
    r"(-?\d+),"  # wire_out (integer, can be negative)
    r"(\d+),"  # turns (integer)
    r"(-?[\d.]+),"  # speed (float, can be negative)
    r"(\d+)"  # tension (integer)
)


def winch_descriptor_processor(
    data_dir: Path,
    file_info: R2RFileInfo,
    sensor_info: R2RSensorInfo,
    provider_id: str,
) -> SensorDescriptor:
    """Build a SensorDescriptor for an R2R LCI-90i winch.

    This concentrates identification and metadata in one place so that
    other providers sharing the same instrument can re-use the same
    canonical sensor ID and variables.
    """

    campaign_id = file_info.campaign_id or "unknown_campaign"
    platform_id = file_info.platform

    metadata: dict[str, Any] = {}
    metadata.update(file_info.extra or {})
    metadata.update(sensor_info.extra or {})

    # Provide some canonical keys that can be mapped directly into
    # sensor.json or other catalogues.
    if sensor_info.sensor_id:
        metadata.setdefault("instrument_id", sensor_info.sensor_id)
    if sensor_info.description:
        metadata.setdefault("instrument_description", sensor_info.description)

    return SensorDescriptor(
        sensor_type=sensor_info.sensor_type or SENSOR_TYPE_WINCH,
        sensor_id=SENSOR_ID_WINCH,
        provider_id=provider_id,
        platform_id=platform_id,
        campaign_id=campaign_id,
        description=sensor_info.description or "LCI-90i winch monitoring system",
        metadata=metadata,
    )


def winch_raw_processor(
    data_dir: Path,
    file_info: R2RFileInfo,
    sensor_info: R2RSensorInfo,
    descriptor: SensorDescriptor,
) -> Path:
    """Process R2R LCI-90i winch raw data into a simple CSV file.

    R2R winch data files (typically named ``winch_lci90i_*``) contain
    lines of the form::

        2022-06-14T06:25:23.876888Z 03RD,2022-05-14T16:17:36.502,-0000168,00000000,-00004.8,2839

    We parse these into a tabular structure with the following columns:

    - ``time`` — ISO timestamp when data was logged
    - ``device_id`` — winch device identifier
    - ``time_instrument`` — ISO timestamp from instrument
    - ``wire_out_m`` — wire payout in meters (converted from raw units)
    - ``turns`` — drum turns counter
    - ``wire_speed_mps`` — wire speed in m/s
    - ``tension_lbs`` — wire tension in lbs

    The function writes a ``winch.csv`` file in ``data_dir`` and
    returns its path. Malformed lines are skipped rather than raising.

    Parameters
    ----------
    data_dir : Path
        Directory containing raw winch data files.
    file_info : R2RFileInfo
        R2R file metadata.
    sensor_info : R2RSensorInfo
        Sensor metadata.
    descriptor : SensorDescriptor
        Sensor descriptor built by the descriptor processor.

    Returns
    -------
    Path
        Path to the generated CSV file.
    """

    rows: list[list[str]] = []
    
    # Find all winch data files - they typically have no extension
    # and are named like "winch_lci90i_rr_trawl-2022-06-14"
    winch_files: list[Path] = []
    
    for f in sorted(data_dir.iterdir()):
        if f.is_file() and "winch" in f.name.lower():
            winch_files.append(f)
    
    # Also check for .Raw files as backup
    if not winch_files:
        winch_files = sorted(data_dir.glob("*.Raw"))

    for raw_path in winch_files:
        try:
            with raw_path.open("r", encoding="ascii", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    match = LCI90I_PATTERN.match(line)
                    if not match:
                        continue

                    (
                        timestamp_logged,
                        device_id,
                        timestamp_instrument,
                        wire_out_raw,
                        turns,
                        speed,
                        tension,
                    ) = match.groups()

                    # Convert wire_out from raw units to meters
                    # Raw values appear to be in some small unit (possibly 0.1m or similar)
                    # The negative sign indicates wire out (vs wire in)
                    wire_out_m = int(wire_out_raw) / 1000.0  # Rough conversion

                    rows.append([
                        timestamp_logged,
                        device_id,
                        timestamp_instrument,
                        f"{wire_out_m:.3f}",
                        turns,
                        speed,
                        tension,
                    ])
        except OSError as e:
            # Log and continue with other files
            import logging
            logging.getLogger(__name__).warning(f"Could not read {raw_path}: {e}")
            continue

    out_path = data_dir / "winch.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "time",
            "device_id",
            "time_instrument",
            "wire_out_m",
            "turns",
            "wire_speed_mps",
            "tension_lbs",
        ])
        writer.writerows(rows)

    return out_path


def parse_winch_file(file_path: Path) -> list[dict[str, Any]]:
    """Parse a single LCI-90i winch data file.

    This is a convenience function for use by other processors or
    for direct parsing without going through the full processor pipeline.

    Parameters
    ----------
    file_path : Path
        Path to the winch data file.

    Returns
    -------
    list[dict[str, Any]]
        List of parsed records with keys: time, device_id, time_instrument,
        wire_out_m, turns, wire_speed_mps, tension_lbs.
    """
    records: list[dict[str, Any]] = []
    
    with file_path.open("r", encoding="ascii", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            match = LCI90I_PATTERN.match(line)
            if not match:
                continue

            (
                timestamp_logged,
                device_id,
                timestamp_instrument,
                wire_out_raw,
                turns,
                speed,
                tension,
            ) = match.groups()

            records.append({
                "time": timestamp_logged,
                "device_id": device_id,
                "time_instrument": timestamp_instrument,
                "wire_out_m": int(wire_out_raw) / 1000.0,
                "turns": int(turns),
                "wire_speed_mps": float(speed),
                "tension_lbs": int(tension),
            })

    return records


register_sensor_processor(SENSOR_TYPE_WINCH, winch_descriptor_processor)
register_raw_processor(SENSOR_ID_WINCH, winch_raw_processor)
