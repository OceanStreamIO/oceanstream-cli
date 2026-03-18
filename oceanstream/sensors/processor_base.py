"""Base types and interfaces for generic sensor processors.

These abstractions are intentionally light-weight so they can be used
by multiple providers (e.g. Saildrone, R2R, others) when they share
the same underlying sensor data format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class FileInfo:
    """Provider-agnostic file metadata.

    Any provider can populate this with campaign/platform identifiers
    and freeform extras.  Provider-specific subclasses (e.g.
    ``R2RFileInfo``) may add fields but processors only depend on this
    base type.
    """

    campaign_id: str | None = None
    cruise_id: str | None = None
    platform: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class SensorInfo:
    """Provider-agnostic sensor metadata.

    Mirrors the minimal set of keys that every sensor processor needs.
    Provider-specific subclasses (e.g. ``R2RSensorInfo``) may add
    fields but processors only depend on this base type.
    """

    sensor_type: str | None = None
    sensor_id: str | None = None
    description: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class SensorDescriptor:
    """Describes a sensor instance for the sensor catalogue.

    This is *not* the full STAC or GeoParquet schema – it is a
    compact description used to update the sensor catalogue entries
    for a given provider.
    """

    sensor_type: str
    sensor_id: str | None
    provider_id: str
    platform_id: str | None
    campaign_id: str
    description: str | None
    metadata: dict[str, str]


class SensorProcessor(Protocol):
    """Protocol for per-sensor processors.

    A processor receives a directory containing raw data for a sensor
    plus generic metadata objects.  Provider-specific subtypes of
    ``FileInfo`` / ``SensorInfo`` are accepted transparently.
    """

    def __call__(
        self,
        data_dir: Path,
        file_info: FileInfo,
        sensor_info: SensorInfo,
        provider_id: str,
    ) -> SensorDescriptor:  # pragma: no cover - structural protocol
        ...


class RawProcessor(Protocol):
    """Protocol for per-sensor raw data processors.

    A raw processor is responsible for converting provider-specific raw
    files for a *particular sensor* into a standardised intermediate
    representation (typically CSV/GeoCSV) that the rest of the pipeline
    can consume.

    The protocol is intentionally minimal so that the same raw processor
    can be re-used by different providers as long as they supply
    compatible metadata objects.
    """

    def __call__(
        self,
        data_dir: Path,
        file_info: FileInfo,
        sensor_info: SensorInfo,
        descriptor: SensorDescriptor,
    ) -> Path:  # pragma: no cover - structural protocol
        """Process raw data and return path to standardised output file."""
        ...
