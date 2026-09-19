"""Scene — one acquisition, and nothing else.

A :class:`Scene` is the library's input contract: atmospherically-corrected
surface reflectance on a grid, plus the geometry needed to interpret it. How the
scene arrived — CDSE, Planetary Computer, an EarthStudio product feed, a manual
ACOLITE run — is not the library's business, and deliberately so: scene
discovery is a solved problem owned by other systems, and reimplementing it here
would couple the physics to a catalogue API.

Solar geometry lives here, not on the site
------------------------------------------
The prototype stored ``solar_zenith_fallback_s2_deg`` on its site profile, which
made the fallback a property of the coastline. It is not: the same coast at the
same latitude has a solar zenith that varies by tens of degrees across the year,
and the Lee model's air–water geometry term depends on it directly. It is a
per-acquisition value, so it is a field here, and the fallback is an explicit
argument the caller has to supply rather than something silently inherited.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from oceanstream.coastal.acolite import (
    L2R_RHOS_GLOB,
    L2W_RRS_GLOB,
    SZA_GLOB,
    wavelength_of,
)
from oceanstream.coastal.io.rasters import RasterGrid, read_band
from oceanstream.coastal.sensors import SensorProfile, get_sensor

logger = logging.getLogger(__name__)

#: ACOLITE embeds the acquisition date in its output filenames as
#: ``<SENSOR>_<YYYY>_<MM>_<DD>_...``.
_DATE_IN_NAME = re.compile(r"(\d{4})[_-](\d{2})[_-](\d{2})")


@dataclass
class Scene:
    """One atmospherically-corrected acquisition over an AOI.

    ``rrs_above`` is above-water remote-sensing reflectance in sr⁻¹, shaped
    ``(n_bands, height, width)`` and ordered by ascending wavelength. ``rhos`` is
    the surface reflectance it was derived from, kept because the masking stage
    thresholds on rhos directly (SWIR screens, glint, cloud) and dividing by π
    twice is an easy mistake to make silently.
    """

    sensor: SensorProfile
    acquisition_date: dt.date
    wavelengths_nm: np.ndarray
    rrs_above: np.ndarray
    #: Surface reflectance, or ``None`` when the scene was built from Rrs alone.
    #: Required positionally rather than defaulted: whether rhos exists is a
    #: fact about the scene's provenance, and callers should have to state it.
    rhos: np.ndarray | None
    grid: RasterGrid
    solar_zenith_deg: float
    solar_azimuth_deg: float | None = None
    #: How ``solar_zenith_deg`` was obtained: ``"settings"``, ``"sza_raster"``
    #: or ``"fallback"``. Recorded because a fallback value is an assumption,
    #: and an assumption that does not appear in the run report is a silent one.
    solar_zenith_source: str = "unknown"
    #: True when Rrs was derived as rhos/π rather than read from an L2W product.
    rrs_derived_from_rhos: bool = False
    source_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    acquisition_datetime: dt.datetime | None = None

    def __post_init__(self) -> None:
        wl = np.asarray(self.wavelengths_nm)
        if not np.all(np.isfinite(wl)) or np.any(np.diff(wl) <= 0):
            raise ValueError("Scene wavelengths must be finite, unique, and ascending.")
        if self.acquisition_datetime is not None:
            if self.acquisition_datetime.utcoffset() is None:
                raise ValueError("acquisition_datetime requires a timezone.")
            self.acquisition_datetime = self.acquisition_datetime.astimezone(dt.UTC)
            if self.acquisition_datetime.date() != self.acquisition_date:
                raise ValueError("Acquisition date and UTC acquisition time disagree.")
        if self.rrs_above.ndim != 3:
            raise ValueError(
                f"Scene.rrs_above must be (n_bands, H, W); got shape {self.rrs_above.shape}."
            )
        if len(self.wavelengths_nm) != self.rrs_above.shape[0]:
            raise ValueError(
                f"Scene has {len(self.wavelengths_nm)} wavelengths but "
                f"{self.rrs_above.shape[0]} bands."
            )
        if self.rrs_above.shape[1:] != self.grid.shape:
            raise ValueError(
                f"Scene raster shape {self.rrs_above.shape[1:]} does not match its "
                f"grid {self.grid.shape}."
            )
        if self.rhos is not None and self.rhos.shape != self.rrs_above.shape:
            raise ValueError(
                f"Scene.rhos {self.rhos.shape} and Scene.rrs_above "
                f"{self.rrs_above.shape} must have the same shape — they are the "
                "same bands over the same grid, and every band index is shared "
                "between them."
            )
        if not 0.0 <= self.solar_zenith_deg < 90.0:
            raise ValueError(
                f"solar_zenith_deg must be in [0, 90); got {self.solar_zenith_deg}. "
                "A value at or past 90° is a night-time or invalid acquisition."
            )

    # -- Derived views ------------------------------------------------------

    @property
    def n_bands(self) -> int:
        return int(self.rrs_above.shape[0])

    @property
    def shape(self) -> tuple[int, int]:
        return self.grid.shape

    def band(self, target_nm: float, tolerance_nm: float | None = None) -> np.ndarray:
        """The band nearest ``target_nm``, matched within the sensor's tolerance."""
        idx = self.sensor.band_index(self.wavelengths_nm, target_nm, tolerance_nm)
        if idx is None:
            raise KeyError(
                f"No band within "
                f"{tolerance_nm or self.sensor.band_match_tolerance_nm:g} nm of "
                f"{target_nm:g} nm. Scene carries "
                f"{[round(float(w), 1) for w in self.wavelengths_nm]} nm."
            )
        return np.asarray(self.rrs_above[idx])

    def rhos_band(self, target_nm: float, tolerance_nm: float | None = None) -> np.ndarray:
        """As :meth:`band`, but surface reflectance rather than Rrs."""
        if self.rhos is None:
            raise ValueError(
                "Scene carries no rhos rasters. Load it from an ACOLITE L2R "
                "directory, or pass rhos= to Scene.from_arrays(). Deglinting and "
                "the SWIR deep-water screen both work on rhos, not Rrs."
            )
        idx = self.sensor.band_index(self.wavelengths_nm, target_nm, tolerance_nm)
        if idx is None:
            raise KeyError(
                f"No rhos band within "
                f"{tolerance_nm or self.sensor.band_match_tolerance_nm:g} nm of "
                f"{target_nm:g} nm."
            )
        return np.asarray(self.rhos[idx])

    def validate(self) -> list[str]:
        """Band-set problems, worst first. Empty means fully usable."""
        return self.sensor.validate_scene_bands(self.wavelengths_nm)

    def describe(self) -> dict[str, Any]:
        """Provenance record for a run report or STAC item."""
        return {
            "sensor": self.sensor.name,
            "acquisition_date": self.acquisition_date.isoformat(),
            "acquisition_datetime": self.acquisition_datetime.isoformat()
            if self.acquisition_datetime
            else None,
            "wavelengths_nm": [round(float(w), 1) for w in self.wavelengths_nm],
            "solar_zenith_deg": round(self.solar_zenith_deg, 3),
            "solar_zenith_source": self.solar_zenith_source,
            "solar_azimuth_deg": (
                None if self.solar_azimuth_deg is None else round(self.solar_azimuth_deg, 3)
            ),
            "rrs_derived_from_rhos": self.rrs_derived_from_rhos,
            "grid": self.grid.to_dict(),
            "source_dir": None if self.source_dir is None else str(self.source_dir),
            "band_warnings": self.validate(),
        }

    # -- Loading ------------------------------------------------------------

    @classmethod
    def from_acolite_dir(
        cls,
        directory: str | Path,
        sensor: SensorProfile | str = "sentinel2",
        acquisition_date: dt.date | None = None,
        solar_zenith_fallback_deg: float | None = None,
        solar_azimuth_deg: float | None = None,
        acquisition_datetime: dt.datetime | None = None,
    ) -> Scene:
        """Load a scene from an ACOLITE output directory.

        ACOLITE writes one GeoTIFF per band with the wavelength in the filename.
        When only L2R is present — the default — Rrs is derived as ``rhos / π``
        rather than forcing an L2W re-run, which is the same quantity by
        the ACOLITE surface-reflectance convention (rho_s = pi * Rrs).
        """
        directory = Path(directory)
        profile = sensor if isinstance(sensor, SensorProfile) else get_sensor(sensor)
        if not directory.is_dir():
            raise FileNotFoundError(
                f"ACOLITE output directory not found: {directory}. Run "
                "oceanstream.coastal.acolite.correct_scene() first, or point at an "
                "existing L2R directory."
            )

        rrs_paths = sorted(directory.glob(L2W_RRS_GLOB), key=wavelength_of)
        rhos_paths = sorted(directory.glob(L2R_RHOS_GLOB), key=wavelength_of)
        if not rrs_paths and not rhos_paths:
            raise FileNotFoundError(
                f"No {L2W_RRS_GLOB} or {L2R_RHOS_GLOB} files in {directory}. "
                "Either ACOLITE did not finish, or it wrote NetCDF rather than "
                "GeoTIFF — set l2r_export_geotiff=True in the settings file."
            )

        derived = not rrs_paths
        all_paths = rrs_paths + rhos_paths
        identities = {re.split(r"_L2[RW]_", p.stem)[0] for p in all_paths}
        if len(identities) != 1:
            raise ValueError(
                "Mixed scene identities in ACOLITE directory; use one acquisition per directory."
            )
        for paths in (rrs_paths, rhos_paths):
            centres = [wavelength_of(p) for p in paths]
            if len(centres) != len(set(centres)):
                raise ValueError("Duplicate wavelength in ACOLITE directory.")
        if (
            rrs_paths
            and rhos_paths
            and [wavelength_of(p) for p in rrs_paths] != [wavelength_of(p) for p in rhos_paths]
        ):
            raise ValueError("Rrs and rhos wavelength sets differ; export matching bands.")
        source_paths = rhos_paths if derived else rrs_paths
        wavelengths = np.array([wavelength_of(p) for p in source_paths], dtype=float)

        bands = []
        grid: RasterGrid | None = None
        for path in source_paths:
            data, band_grid = read_band(path)
            if grid is None:
                grid = band_grid
            elif not band_grid.matches(grid):
                raise ValueError(
                    f"{path.name} is on a different grid from {source_paths[0].name}. "
                    "ACOLITE should emit a single grid per run; a mixed set means two "
                    "runs have been written into the same directory."
                )
            bands.append(data / np.float32(np.pi) if derived else data)
        assert grid is not None
        rrs_above = np.stack(bands, axis=0)

        if rhos_paths:
            surface_bands = []
            for path in rhos_paths:
                data, surface_grid = read_band(path)
                if not surface_grid.matches(grid):
                    raise ValueError(f"{path.name}: rhos and Rrs are on a different grid.")
                surface_bands.append(data)
            rhos = np.stack(surface_bands, axis=0)
        else:
            rhos = rrs_above * np.float32(np.pi)
        if not derived:
            paired = np.isfinite(rrs_above) & np.isfinite(rhos)
            if not np.allclose(rrs_above[paired] * np.pi, rhos[paired], rtol=1e-3, atol=1e-7):
                raise ValueError(
                    "ACOLITE reflectance convention mismatch: rhos must equal pi * Rrs."
                )

        import rasterio

        with rasterio.open(source_paths[0]) as source:
            tags = source.tags()
        metadata = {
            key: tags[f"NC_GLOBAL#{key}"]
            for key in ("acolite_version", "isodate", "sza")
            if f"NC_GLOBAL#{key}" in tags
        }
        if "sza" in metadata:
            zenith, zenith_source = float(metadata["sza"]), "acolite_metadata"
        else:
            zenith, zenith_source = _resolve_solar_zenith(directory, solar_zenith_fallback_deg)
        observed_time = (
            dt.datetime.fromisoformat(metadata["isodate"].replace("Z", "+00:00"))
            if "isodate" in metadata
            else _time_from_filename(source_paths[0])
        )
        date = acquisition_date or _date_from_filenames(source_paths)
        if date is None:
            raise ValueError(
                f"Could not read an acquisition date from the filenames in "
                f"{directory}. Pass acquisition_date explicitly."
            )

        scene = cls(
            sensor=profile,
            acquisition_date=date,
            wavelengths_nm=wavelengths,
            rrs_above=rrs_above,
            rhos=rhos,
            grid=grid,
            solar_zenith_deg=zenith,
            solar_azimuth_deg=solar_azimuth_deg,
            solar_zenith_source=zenith_source,
            rrs_derived_from_rhos=derived,
            source_dir=directory,
            acquisition_datetime=acquisition_datetime or observed_time,
            metadata=metadata,
        )
        for warning in scene.validate():
            logger.warning("scene band check: %s", warning)
        return scene

    @classmethod
    def from_arrays(
        cls,
        wavelengths_nm: Sequence[float],
        rrs_above: np.ndarray,
        grid: RasterGrid,
        solar_zenith_deg: float,
        sensor: SensorProfile | str = "sentinel2",
        acquisition_date: dt.date | None = None,
        rhos: np.ndarray | None = None,
        metadata: Mapping[str, Any] | None = None,
        acquisition_datetime: dt.datetime | None = None,
    ) -> Scene:
        """Build a scene from in-memory arrays.

        The entry point for callers whose atmospheric correction happened
        elsewhere — a Prefect flow, a Dask graph, a test fixture.

        ``rhos`` is required explicitly for arbitrary external arrays because
        their reflectance convention is not known. The ACOLITE loader can
        derive it using ACOLITE's documented ``rhos = pi * Rrs`` convention.
        """
        rrs = np.asarray(rrs_above, dtype=np.float32)
        return cls(
            sensor=sensor if isinstance(sensor, SensorProfile) else get_sensor(sensor),
            acquisition_date=acquisition_date
            or (
                acquisition_datetime.astimezone(dt.UTC).date()
                if acquisition_datetime
                else dt.date.today()
            ),
            wavelengths_nm=np.asarray(wavelengths_nm, dtype=float),
            rrs_above=rrs,
            rhos=None if rhos is None else np.asarray(rhos, np.float32),
            grid=grid,
            solar_zenith_deg=solar_zenith_deg,
            solar_zenith_source="caller",
            rrs_derived_from_rhos=False,
            metadata=dict(metadata or {}),
            acquisition_datetime=acquisition_datetime,
        )


def _time_from_filename(path: Path) -> dt.datetime | None:
    match = re.search(r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})", path.name)
    return (
        dt.datetime.strptime(match[1], "%Y_%m_%d_%H_%M_%S").replace(tzinfo=dt.UTC)
        if match
        else None
    )


def _resolve_solar_zenith(directory: Path, fallback_deg: float | None) -> tuple[float, str]:
    """Solar zenith from the settings sidecar, then the SZA raster, then fallback.

    In that order because each step is a degree less trustworthy than the last:
    the sidecar is what ACOLITE actually used, the raster mean is the AOI average
    of what it used, and a fallback is a guess. The source is returned alongside
    so the run report can say which one applied.
    """
    candidates = [
        directory / name
        for name in ("settings.txt", "acolite_run_settings.txt", "acolite_settings.txt")
    ]
    candidates.extend(sorted(directory.glob("*_l2r_settings.txt")))
    for path in candidates:
        if not path.exists():
            continue
        for line in reversed(path.read_text().splitlines()):
            lowered = line.lower().strip()
            if lowered.startswith(("sun_zenith", "sza")):
                try:
                    return float(line.split("=")[-1].strip()), "settings"
                except (ValueError, IndexError):
                    continue

    sza_paths = sorted(directory.glob(SZA_GLOB))
    if sza_paths:
        sza, _ = read_band(sza_paths[0])
        valid = np.isfinite(sza) & (sza > 0.0) & (sza < 90.0)
        if valid.any():
            value = float(np.mean(sza[valid]))
            logger.info("read solar zenith %.2f° from %s", value, sza_paths[0].name)
            return value, "sza_raster"

    if fallback_deg is None:
        raise ValueError(
            f"No solar zenith in {directory}: no settings sidecar, no "
            f"{SZA_GLOB} raster, and no solar_zenith_fallback_deg supplied. "
            "The Lee inversion's air-water geometry term needs it; pass "
            "--solar-zenith or re-run ACOLITE with geometry export enabled."
        )
    logger.warning(
        "solar zenith not found in %s — using the supplied fallback of %.1f°. "
        "This is an assumption and is recorded as such in the run report.",
        directory,
        fallback_deg,
    )
    return fallback_deg, "fallback"


def _date_from_filenames(paths: Sequence[Path]) -> dt.date | None:
    for path in paths:
        match = _DATE_IN_NAME.search(path.stem)
        if match:
            year, month, day = (int(g) for g in match.groups())
            try:
                return dt.date(year, month, day)
            except ValueError:
                continue
    return None
