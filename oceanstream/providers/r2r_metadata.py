"""Metadata parsing helpers for R2R archives.

These utilities parse the small text files that accompany an R2R
archive (``file-info.txt`` and ``bag-info.txt``) into light-weight
Python data structures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class R2RFileInfo:
	"""Parsed contents of an ``R2R file-info.txt`` file.

	Only a small subset of potential keys are promoted to explicit
	attributes; all remaining keys are preserved in ``extra``.
	"""

	campaign_id: str | None = None
	cruise_id: str | None = None
	platform: str | None = None
	start_time: str | None = None
	end_time: str | None = None
	extra: dict[str, str] | None = None


@dataclass
class R2RSensorInfo:
	"""Parsed contents of an ``R2R bag-info.txt`` file.

	The exact key names may vary between archives; we capture a
	sensor-type-like field plus the raw key/value mapping in ``extra``.
	"""

	sensor_type: str | None = None
	sensor_id: str | None = None
	description: str | None = None
	extra: dict[str, str] | None = None


def _parse_simple_kv_file(path: Path) -> dict[str, str]:
	"""Parse a simple ``key: value`` style text file into a dict.

	Lines that do not contain a ``:`` separator or that are empty are
	ignored. Keys are stripped of surrounding whitespace; the raw key
	text is preserved (case-sensitive) for the caller, but a
	lowercased version is often used for matching known fields.
	"""

	result: dict[str, str] = {}
	text = path.read_text(encoding="utf-8", errors="ignore")
	for raw_line in text.splitlines():
		line = raw_line.strip()
		if not line or ":" not in line:
			continue
		key, value = line.split(":", 1)
		result[key.strip()] = value.strip()
	return result


def parse_file_info(path: Path) -> R2RFileInfo:
	"""Parse an R2R ``file-info.txt`` file into :class:`R2RFileInfo`.

	The exact key names vary a bit across sources; we try a small
	collection of common variants and store everything in ``extra``.
	"""

	if not path.is_file():
		raise FileNotFoundError(f"R2R file-info.txt not found: {path}")

	kv = _parse_simple_kv_file(path)
	extra = dict(kv)

	def pop_first(*keys: str) -> str | None:
		for k in keys:
			if k in extra:
				return extra.pop(k)
		return None

	campaign_id = pop_first("Campaign", "campaign_id", "campaign")
	cruise_id = pop_first("Cruise", "cruise_id")
	platform = pop_first("Platform", "Ship", "Vessel")
	start_time = pop_first("Start time", "StartTime", "start_time")
	end_time = pop_first("End time", "EndTime", "end_time")

	return R2RFileInfo(
		campaign_id=campaign_id,
		cruise_id=cruise_id,
		platform=platform,
		start_time=start_time,
		end_time=end_time,
		extra=extra or None,
	)


def parse_bag_info(path: Path) -> R2RSensorInfo:
	"""Parse an R2R ``bag-info.txt`` file into :class:`R2RSensorInfo`.

	``bag-info.txt`` typically contains information about the sensor
	and the bagged data. We extract a best-effort sensor type/id and
	description, preserving all keys in ``extra`` for future use.
	
	Also extracts R2R-specific metadata like cruise ID and DOIs for
	platform identification.
	"""

	if not path.is_file():
		raise FileNotFoundError(f"R2R bag-info.txt not found: {path}")

	kv = _parse_simple_kv_file(path)
	extra = dict(kv)

	def pop_first(*keys: str) -> str | None:
		for k in keys:
			if k in extra:
				return extra.pop(k)
		return None

	# Extract sensor information
	sensor_type = pop_first("Sensor Type", "sensor_type", "Instrument", "instrument", 
	                        "R2R-DeviceType", "DeviceType")
	sensor_id = pop_first("Sensor ID", "sensor_id", "SerialNumber", "serial_number",
	                      "R2R-DeviceModel", "DeviceModel")
	description = pop_first("Description", "description", "Internal-Sender-Description")

	return R2RSensorInfo(
		sensor_type=sensor_type,
		sensor_id=sensor_id,
		description=description,
		extra=extra or None,
	)

