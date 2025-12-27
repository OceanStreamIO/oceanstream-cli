from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


def _parse_simple_kv_file(path: Path) -> Dict[str, str]:
    """Parse a simple `key: value` text file into a dictionary."""

    text = path.read_text(encoding="utf-8")
    result: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        result[key.strip()] = value.strip()
    return result


@dataclass
class R2RFileInfo:
    """Structured representation of `file-info.txt` contents."""

    campaign_id: Optional[str] = None
    cruise_id: Optional[str] = None
    platform: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class R2RSensorInfo:
    """Structured representation of `bag-info.txt` contents."""

    sensor_type: Optional[str] = None
    sensor_id: Optional[str] = None
    description: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)


def parse_file_info(path: Path) -> R2RFileInfo:
    """Parse an R2R `file-info.txt` file into :class:`R2RFileInfo`."""

    if not path.exists():
        raise FileNotFoundError(path)

    raw = _parse_simple_kv_file(path)

    def pop_first(keys: list[str]) -> Optional[str]:
        for key in keys:
            if key in raw:
                return raw.pop(key)
        return None

    campaign_id = pop_first(["Campaign", "campaign_id", "campaign"])
    cruise_id = pop_first(["Cruise", "cruise_id"])
    platform = pop_first(["Platform", "Ship", "Vessel"])
    start_time = pop_first(["Start time", "StartTime", "start_time"])
    end_time = pop_first(["End time", "EndTime", "end_time"])

    return R2RFileInfo(
        campaign_id=campaign_id,
        cruise_id=cruise_id,
        platform=platform,
        start_time=start_time,
        end_time=end_time,
        extra=raw,
    )


def parse_bag_info(path: Path) -> R2RSensorInfo:
    """Parse an R2R `bag-info.txt` file into :class:`R2RSensorInfo`."""

    if not path.exists():
        raise FileNotFoundError(path)

    raw = _parse_simple_kv_file(path)

    def pop_first(keys: list[str]) -> Optional[str]:
        for key in keys:
            if key in raw:
                return raw.pop(key)
        return None

    sensor_type = pop_first([
        "R2R-DeviceType",  # R2R standard field for device type
        "Sensor Type",
        "sensor_type",
        "Instrument",
        "instrument",
    ])
    sensor_id = pop_first([
        "R2R-DeviceModel",  # R2R standard field for device model
        "Sensor ID",
        "sensor_id",
        "SerialNumber",
        "serial_number",
    ])
    description = pop_first([
        "Internal-Sender-Description",  # R2R description field
        "Description",
        "description",
    ])

    return R2RSensorInfo(
        sensor_type=sensor_type,
        sensor_id=sensor_id,
        description=description,
        extra=raw,
    )
