"""R2R CTD (SeaBird SBE-911+) sensor processor.

This module provides processing for SeaBird CTD data from R2R archives.
Uses the official seabirdscientific library for hex file parsing.

Supported instruments:
- SBE 911plus / 917plus (ship-based profiling CTD)

Input formats:
- .tar.gz: R2R BagIt archive containing CTD data
- .hex: Raw hexadecimal data from CTD (individual file)
- .hdr: Header file with metadata (lat/lon, time, etc.)
- .xmlcon: XML configuration with sensor calibration coefficients

The processor can accept:
1. A directory containing extracted CTD files
2. A .tar.gz archive (will be extracted automatically)
3. A single .hex file (will find associated .hdr/.xmlcon in same directory)

Output:
- CSV file with scientific values (temperature, conductivity, pressure, salinity, etc.)
"""

from __future__ import annotations

import logging
import re
import shutil
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import numpy as np
    import pandas as pd
except ImportError:
    raise ImportError(
        "numpy and pandas are required for CTD processing. "
        "Install with: pip install oceanstream[geotrack]"
    )

try:
    import seabirdscientific.conversion as sbs_conv
    import seabirdscientific.instrument_data as sbs_id
    from seabirdscientific.cal_coefficients import (
        ConductivityCoefficients,
        Oxygen43Coefficients,
        PressureDigiquartzCoefficients,
        TemperatureFrequencyCoefficients,
    )
except ImportError:
    sbs_id = None
    sbs_conv = None

try:
    import gsw as _gsw
except ImportError:
    _gsw = None

if TYPE_CHECKING:
    from oceanstream.providers.r2r.r2r_metadata import R2RFileInfo, R2RSensorInfo
    from oceanstream.sensors.processor_base import SensorDescriptor

logger = logging.getLogger(__name__)

# Sensor type identifiers
SENSOR_TYPE_CTD = "ctd"
SENSOR_ID_CTD = "sbe-911plus"


@dataclass
class CTDInput:
    """Container for CTD input source (archive, directory, or single file)."""

    path: Path
    is_archive: bool = False
    is_single_file: bool = False
    extract_dir: Path | None = None
    data_dir: Path | None = None
    _temp_dir: Path | None = None

    def __post_init__(self):
        """Determine input type and extract if needed."""
        if self.path.is_file():
            if self.path.suffix == '.gz' or self.path.name.endswith('.tar.gz'):
                self.is_archive = True
            elif self.path.suffix == '.hex':
                self.is_single_file = True
                self.data_dir = self.path.parent
            else:
                raise ValueError(f"Unsupported file type: {self.path}")
        elif self.path.is_dir():
            self.data_dir = self.path
        else:
            raise FileNotFoundError(f"Input not found: {self.path}")

    def extract(self, work_dir: Path | None = None) -> Path:
        """Extract archive if needed and return data directory.

        Args:
            work_dir: Directory to extract to. If None, uses temp directory.

        Returns:
            Path to directory containing CTD data files
        """
        if not self.is_archive:
            return self.data_dir or self.path

        # Use provided work_dir or create temp directory
        if work_dir:
            extract_dir = work_dir / self.path.stem.replace('.tar', '')
            extract_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._temp_dir = Path(tempfile.mkdtemp(prefix='ctd_'))
            extract_dir = self._temp_dir

        logger.info(f"Extracting archive {self.path.name} to {extract_dir}")

        # Extract archive
        with tarfile.open(self.path, 'r:gz') as tf:
            tf.extractall(extract_dir)

        self.extract_dir = extract_dir

        # Find data directory (R2R BagIt structure has 'data' folder)
        data_dir = None
        for path in extract_dir.rglob('*'):
            if path.is_dir() and path.name == 'data':
                data_dir = path
                break

        # If no 'data' dir, look for hex files anywhere
        if data_dir is None:
            hex_files = list(extract_dir.rglob('*.hex'))
            if hex_files:
                data_dir = hex_files[0].parent

        # Fall back to extract dir
        if data_dir is None:
            data_dir = extract_dir

        self.data_dir = data_dir
        logger.info(f"CTD data directory: {data_dir}")

        return data_dir

    def cleanup(self):
        """Remove temporary extraction directory if created."""
        if self._temp_dir and self._temp_dir.exists():
            logger.debug(f"Cleaning up temp directory: {self._temp_dir}")
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None


@dataclass
class CTDCast:
    """Container for a single CTD cast's metadata and data."""

    cast_id: str
    cruise_id: str
    hex_file: Path
    hdr_file: Path | None = None
    xmlcon_file: Path | None = None

    # Metadata from header file
    latitude: float | None = None
    longitude: float | None = None
    start_time: datetime | None = None
    station: str | None = None

    # Sensor configuration from XMLCON
    sensors: list[dict[str, Any]] = field(default_factory=list)

    # Raw data
    raw_data: pd.DataFrame | None = None

    # Processed data
    processed_data: pd.DataFrame | None = None


def parse_hdr_file(hdr_path: Path) -> dict[str, Any]:
    """Parse a SeaBird .hdr file to extract metadata.

    Args:
        hdr_path: Path to the .hdr file

    Returns:
        Dictionary with parsed metadata including lat, lon, time, station
    """
    metadata: dict[str, Any] = {}

    with open(hdr_path, encoding='utf-8', errors='replace') as f:
        content = f.read()

    # Parse NMEA Latitude (e.g., "* NMEA Latitude = 34 27.76 N")
    lat_match = re.search(r'\*\s*NMEA Latitude\s*=\s*(\d+)\s+(\d+\.?\d*)\s*([NS])', content)
    if lat_match:
        degrees = float(lat_match.group(1))
        minutes = float(lat_match.group(2))
        hemisphere = lat_match.group(3)
        metadata['latitude'] = degrees + minutes / 60
        if hemisphere == 'S':
            metadata['latitude'] = -metadata['latitude']

    # Parse NMEA Longitude (e.g., "* NMEA Longitude = 120 31.31 W")
    lon_match = re.search(r'\*\s*NMEA Longitude\s*=\s*(\d+)\s+(\d+\.?\d*)\s*([EW])', content)
    if lon_match:
        degrees = float(lon_match.group(1))
        minutes = float(lon_match.group(2))
        hemisphere = lon_match.group(3)
        metadata['longitude'] = degrees + minutes / 60
        if hemisphere == 'W':
            metadata['longitude'] = -metadata['longitude']

    # Parse NMEA UTC time (e.g., "* NMEA UTC (Time) = Feb 21 2024  14:44:42")
    time_match = re.search(
        r'\*\s*NMEA UTC \(Time\)\s*=\s*(\w+)\s+(\d+)\s+(\d+)\s+(\d+):(\d+):(\d+)', content
    )
    if time_match:
        month_str = time_match.group(1)
        day = int(time_match.group(2))
        year = int(time_match.group(3))
        hour = int(time_match.group(4))
        minute = int(time_match.group(5))
        second = int(time_match.group(6))

        # Convert month string to number
        months = {
            'Jan': 1,
            'Feb': 2,
            'Mar': 3,
            'Apr': 4,
            'May': 5,
            'Jun': 6,
            'Jul': 7,
            'Aug': 8,
            'Sep': 9,
            'Oct': 10,
            'Nov': 11,
            'Dec': 12,
        }
        month = months.get(month_str, 1)

        try:
            metadata['start_time'] = datetime(year, month, day, hour, minute, second)
        except ValueError:
            logger.warning(f"Could not parse datetime from hdr file: {hdr_path}")

    # Parse station info (e.g., "** Station: BBL Station 8")
    station_match = re.search(r'\*\*\s*Station:\s*(.+)', content)
    if station_match:
        metadata['station'] = station_match.group(1).strip()

    # Parse ship (e.g., "** Ship: Revelle")
    ship_match = re.search(r'\*\*\s*Ship:\s*(.+)', content)
    if ship_match:
        metadata['ship'] = ship_match.group(1).strip()

    # Parse temperature and conductivity serial numbers
    temp_sn_match = re.search(r'\*\s*Temperature SN\s*=\s*(\d+)', content)
    if temp_sn_match:
        metadata['temperature_sn'] = temp_sn_match.group(1)

    cond_sn_match = re.search(r'\*\s*Conductivity SN\s*=\s*(\d+)', content)
    if cond_sn_match:
        metadata['conductivity_sn'] = cond_sn_match.group(1)

    # Parse bytes per scan
    bytes_match = re.search(r'\*\s*Number of Bytes Per Scan\s*=\s*(\d+)', content)
    if bytes_match:
        metadata['bytes_per_scan'] = int(bytes_match.group(1))

    # Parse voltage words
    voltage_match = re.search(r'\*\s*Number of Voltage Words\s*=\s*(\d+)', content)
    if voltage_match:
        metadata['voltage_words'] = int(voltage_match.group(1))

    return metadata


def parse_xmlcon_file(xmlcon_path: Path) -> dict[str, Any]:
    """Parse a SeaBird .xmlcon file to extract sensor configuration.

    Args:
        xmlcon_path: Path to the XMLCON file

    Returns:
        Dictionary with sensor configuration and calibration coefficients
    """
    config: dict[str, Any] = {
        'sensors': [],
        'frequency_channels_suppressed': 0,
        'voltage_words_suppressed': 0,
        'nmea_position_added': False,
        'scan_time_added': False,
    }

    try:
        tree = ET.parse(xmlcon_path)
        root = tree.getroot()

        # Get instrument info
        instrument = root.find('.//Instrument')
        if instrument is not None:
            config['instrument_name'] = instrument.findtext('Name', '')
            config['frequency_channels_suppressed'] = int(
                instrument.findtext('FrequencyChannelsSuppressed', '0')
            )
            config['voltage_words_suppressed'] = int(
                instrument.findtext('VoltageWordsSuppressed', '0')
            )
            config['nmea_position_added'] = instrument.findtext('NmeaPositionDataAdded', '0') == '1'
            config['scan_time_added'] = instrument.findtext('ScanTimeAdded', '0') == '1'

        # Get sensor array
        sensor_array = root.find('.//SensorArray')
        if sensor_array is not None:
            for sensor_elem in sensor_array.findall('Sensor'):
                sensor_info: dict[str, Any] = {
                    'index': int(sensor_elem.get('index', -1)),
                    'sensor_id': sensor_elem.get('SensorID', ''),
                }

                # Try to extract sensor type and serial number
                for child in sensor_elem:
                    if child.tag.endswith('Sensor') or child.tag in [
                        'NotInUse',
                        'UserPolynomialSensor',
                    ]:
                        sensor_info['type'] = child.tag
                        sensor_info['serial_number'] = child.findtext('SerialNumber', '')
                        sensor_info['calibration_date'] = child.findtext('CalibrationDate', '')

                        # Extract calibration coefficients
                        coefs: dict[str, Any] = {}
                        for coef_elem in child.iter():
                            tag = coef_elem.tag
                            # Skip container elements; keep leaf numeric values
                            if tag in (child.tag, 'Coefficients', 'CalibrationCoefficients'):
                                continue
                            if coef_elem.text and coef_elem.text.strip():
                                try:
                                    coefs[tag] = float(coef_elem.text)
                                except ValueError:
                                    coefs[tag] = coef_elem.text.strip()
                        # For conductivity/oxygen that have multiple
                        # <Coefficients equation="N"> blocks, prefer equation="1"
                        # (the more modern G/H/I/J formulation).  Re-parse with
                        # explicit equation selection.
                        coef_blocks = child.findall('Coefficients')
                        if not coef_blocks:
                            coef_blocks = child.findall('CalibrationCoefficients')
                        if len(coef_blocks) > 1:
                            # Pick equation="1" if available
                            for cb in coef_blocks:
                                if cb.get('equation') == '1':
                                    for el in cb:
                                        if el.text and el.text.strip():
                                            try:
                                                coefs[el.tag] = float(el.text)
                                            except ValueError:
                                                coefs[el.tag] = el.text.strip()
                                    break
                        sensor_info['coefficients'] = coefs
                        break

                config['sensors'].append(sensor_info)

    except ET.ParseError as e:
        logger.warning(f"Could not parse XMLCON file {xmlcon_path}: {e}")

    return config


def find_cast_files(data_dir: Path) -> list[CTDCast]:
    """Find and group CTD cast files (hex, hdr, xmlcon) in a directory.

    Args:
        data_dir: Directory containing CTD data files

    Returns:
        List of CTDCast objects with associated files
    """
    casts: dict[str, CTDCast] = {}

    # Find all hex files
    for hex_file in data_dir.glob('*.hex'):
        # Extract cast ID from filename (e.g., "RR2402_cast10.hex" -> "cast10")
        name = hex_file.stem

        # Try to extract cruise ID and cast number
        parts = name.split('_')
        cruise_id = parts[0] if len(parts) > 1 else 'unknown'
        cast_id = parts[1] if len(parts) > 1 else name

        cast = CTDCast(
            cast_id=cast_id.lower(),
            cruise_id=cruise_id,
            hex_file=hex_file,
        )
        casts[cast_id.lower()] = cast

    # Find matching hdr files
    for hdr_file in data_dir.glob('*.hdr'):
        name = hdr_file.stem
        parts = name.split('_')
        cast_id = parts[1].lower() if len(parts) > 1 else name.lower()

        if cast_id in casts:
            casts[cast_id].hdr_file = hdr_file

    # Find matching xmlcon files (case-insensitive)
    for xmlcon_file in data_dir.glob('*.[Xx][Mm][Ll][Cc][Oo][Nn]'):
        name = xmlcon_file.stem
        parts = name.split('_')
        cast_id = parts[1].lower() if len(parts) > 1 else name.lower()

        if cast_id in casts:
            casts[cast_id].xmlcon_file = xmlcon_file

    return list(casts.values())


# ---------------------------------------------------------------------------
# Calibration coefficient builders
# ---------------------------------------------------------------------------


def _find_sensor(
    sensors: list[dict[str, Any]], sensor_type: str, *, start: int = 0
) -> dict[str, Any] | None:
    """Return the first sensor dict whose ``type`` matches *sensor_type*,
    searching from index *start* onward."""
    for s in sensors[start:]:
        if s.get('type') == sensor_type:
            return s
    return None


def _build_temp_coefs(
    sensor: dict[str, Any],
) -> TemperatureFrequencyCoefficients | None:
    """Build ``TemperatureFrequencyCoefficients`` from a parsed sensor dict."""
    c = sensor.get('coefficients', {})
    try:
        return TemperatureFrequencyCoefficients(
            g=float(c['G']),
            h=float(c['H']),
            i=float(c['I']),
            j=float(c['J']),
            f0=float(c['F0']),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Missing temperature calibration coefficients for sensor %s", sensor)
        return None


def _build_cond_coefs(
    sensor: dict[str, Any],
) -> ConductivityCoefficients | None:
    """Build ``ConductivityCoefficients`` from a parsed sensor dict."""
    c = sensor.get('coefficients', {})
    try:
        return ConductivityCoefficients(
            g=float(c['G']),
            h=float(c['H']),
            i=float(c['I']),
            j=float(c['J']),
            cpcor=float(c['CPcor']),
            ctcor=float(c['CTcor']),
            wbotc=float(c.get('WBOTC', 0.0)),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Missing conductivity calibration coefficients for sensor %s", sensor)
        return None


def _build_press_coefs(
    sensor: dict[str, Any],
) -> PressureDigiquartzCoefficients | None:
    """Build ``PressureDigiquartzCoefficients`` from a parsed sensor dict."""
    c = sensor.get('coefficients', {})
    try:
        return PressureDigiquartzCoefficients(
            c1=float(c['C1']),
            c2=float(c['C2']),
            c3=float(c['C3']),
            d1=float(c['D1']),
            d2=float(c['D2']),
            t1=float(c['T1']),
            t2=float(c['T2']),
            t3=float(c['T3']),
            t4=float(c['T4']),
            t5=float(c.get('T5', 0.0)),
            AD590M=float(c.get('AD590M', 0.01278)),
            AD590B=float(c.get('AD590B', -9.256)),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Missing pressure calibration coefficients for sensor %s", sensor)
        return None


def _build_o2_coefs(
    sensor: dict[str, Any],
) -> Oxygen43Coefficients | None:
    """Build ``Oxygen43Coefficients`` from a parsed sensor dict."""
    c = sensor.get('coefficients', {})
    try:
        return Oxygen43Coefficients(
            soc=float(c['Soc']),
            v_offset=float(c.get('offset', c.get('Offset', 0.0))),
            tau_20=float(c.get('Tau20', 0.0)),
            a=float(c['A']),
            b=float(c['B']),
            c=float(c['C']),
            e=float(c['E']),
            d0=float(c['D0']),
            d1=float(c['D1']),
            d2=float(c['D2']),
            h1=float(c['H1']),
            h2=float(c['H2']),
            h3=float(c['H3']),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Missing oxygen calibration coefficients for sensor %s", sensor)
        return None


# SBE 911plus scan rate (Hz)
_SBE911_SCAN_RATE = 24.0


def _calibrate_cast(
    raw_data: pd.DataFrame,
    sensors: list[dict[str, Any]],
    latitude: float,
) -> dict[str, np.ndarray]:
    """Apply seabirdscientific calibration equations to raw hex data.

    Parameters
    ----------
    raw_data : pd.DataFrame
        DataFrame produced by ``sbs_id.read_hex_file``.
    sensors : list[dict]
        Sensor list from ``parse_xmlcon_file``.
    latitude : float
        Station latitude for depth conversion.

    Returns
    -------
    dict[str, np.ndarray]
        Calibrated columns keyed by standard names.
    """
    if sbs_conv is None:
        raise ImportError("seabirdscientific is required for calibrated CTD conversion.")

    result: dict[str, np.ndarray] = {}

    # --- Primary temperature (sensor index 0) ---
    temp_sensor = _find_sensor(sensors, 'TemperatureSensor')
    temp_coefs = _build_temp_coefs(temp_sensor) if temp_sensor else None
    if temp_coefs and 'temperature' in raw_data.columns:
        result['t090C'] = sbs_conv.convert_temperature_frequency(
            raw_data['temperature'].values,
            temp_coefs,
        )

    # --- Pressure (sensor index 2) ---
    press_sensor = _find_sensor(sensors, 'PressureSensor')
    press_coefs = _build_press_coefs(press_sensor) if press_sensor else None
    if (
        press_coefs
        and 'digiquartz pressure' in raw_data.columns
        and 'temperature compensation' in raw_data.columns
    ):
        result['prDM'] = sbs_conv.convert_pressure_digiquartz(
            raw_data['digiquartz pressure'].values,
            raw_data['temperature compensation'].values,
            press_coefs,
            'dbar',
            1.0 / _SBE911_SCAN_RATE,
        )

    # --- Primary conductivity (sensor index 1) ---
    cond_sensor = _find_sensor(sensors, 'ConductivitySensor')
    cond_coefs = _build_cond_coefs(cond_sensor) if cond_sensor else None
    if cond_coefs and 'conductivity' in raw_data.columns and 't090C' in result and 'prDM' in result:
        # convert_conductivity returns mS/cm; store as S/m (÷10)
        result['c0S/m'] = (
            sbs_conv.convert_conductivity(
                raw_data['conductivity'].values,
                result['t090C'],
                result['prDM'],
                cond_coefs,
            )
            / 10.0
        )

    # --- Secondary temperature ---
    temp2_sensor = _find_sensor(sensors, 'TemperatureSensor', start=1)
    # Make sure it's truly a *secondary* sensor (index >= 3)
    if temp2_sensor and temp2_sensor.get('index', 0) < 3:
        temp2_sensor = _find_sensor(sensors, 'TemperatureSensor', start=3)
    temp2_coefs = _build_temp_coefs(temp2_sensor) if temp2_sensor else None
    if temp2_coefs and 'secondary temperature' in raw_data.columns:
        result['t190C'] = sbs_conv.convert_temperature_frequency(
            raw_data['secondary temperature'].values,
            temp2_coefs,
        )

    # --- Secondary conductivity ---
    cond2_sensor = _find_sensor(sensors, 'ConductivitySensor', start=1)
    if cond2_sensor and cond2_sensor.get('index', 0) < 4:
        cond2_sensor = _find_sensor(sensors, 'ConductivitySensor', start=4)
    cond2_coefs = _build_cond_coefs(cond2_sensor) if cond2_sensor else None
    if (
        cond2_coefs
        and 'secondary conductivity' in raw_data.columns
        and 't190C' in result
        and 'prDM' in result
    ):
        result['c1S/m'] = (
            sbs_conv.convert_conductivity(
                raw_data['secondary conductivity'].values,
                result['t190C'],
                result['prDM'],
                cond2_coefs,
            )
            / 10.0
        )

    # --- Salinity (via GSW) ---
    if _gsw is not None:
        if 'c0S/m' in result and 't090C' in result and 'prDM' in result:
            # gsw.SP_from_C expects mS/cm; c0S/m is S/m → multiply by 10
            result['sal00'] = _gsw.SP_from_C(
                result['c0S/m'] * 10.0,
                result['t090C'],
                result['prDM'],
            )
        if 'c1S/m' in result and 't190C' in result and 'prDM' in result:
            result['sal11'] = _gsw.SP_from_C(
                result['c1S/m'] * 10.0,
                result['t190C'],
                result['prDM'],
            )
    else:
        logger.info("gsw not installed — salinity columns omitted")

    # --- Depth ---
    if 'prDM' in result:
        result['depSM'] = sbs_conv.depth_from_pressure(
            result['prDM'],
            latitude,
        )

    # --- Oxygen (SBE43) ---
    o2_sensor = _find_sensor(sensors, 'OxygenSensor')
    o2_coefs = _build_o2_coefs(o2_sensor) if o2_sensor else None
    if o2_coefs and 'sal00' in result and 'prDM' in result and 't090C' in result:
        # Determine which voltage channel carries oxygen.
        # Voltage channels start after the 5 frequency channels
        # (T, C, P, T2, C2).  The oxygen sensor's index tells us which
        # ExtVolt channel it occupies.
        o2_idx = o2_sensor.get('index', -1)
        # Voltage sensors begin at index 5 in a standard SBE-911+ layout
        volt_channel = o2_idx - 5
        volt_col = f'volt {volt_channel}'
        if volt_col in raw_data.columns:
            result['sbeox0ML/L'] = sbs_conv.convert_sbe43_oxygen(
                raw_data[volt_col].values,
                result['t090C'],
                result['prDM'],
                result['sal00'],
                o2_coefs,
                sample_interval=1.0 / _SBE911_SCAN_RATE,
            )

    # --- Voltage-derived auxiliary channels (pass through) ---
    _aux_mapping = {
        'FluoroSeatechWetlabsFLF_Sensor': ('volt 0', 'flS'),
        'WET_LabsCStar': ('volt 1', 'CStarTr0'),
    }
    for sensor_type, (default_volt, col_name) in _aux_mapping.items():
        aux_sensor = _find_sensor(sensors, sensor_type)
        if aux_sensor is not None:
            aux_idx = aux_sensor.get('index', -1)
            volt_col = f'volt {aux_idx - 5}' if aux_idx >= 5 else default_volt
            if volt_col in raw_data.columns:
                result[col_name] = raw_data[volt_col].values

    return result


def process_ctd_cast(
    cast: CTDCast,
    output_dir: Path | None = None,
    calibrate: bool = True,
) -> pd.DataFrame | None:
    """Process a single CTD cast using seabirdscientific library.

    Args:
        cast: CTDCast object with file paths
        output_dir: Optional directory to write CSV output
        calibrate: If True and XMLCON coefficients are available, apply
            calibrated conversion to produce T/C/P/S/depth columns.
            Raw frequency columns are always included.

    Returns:
        DataFrame with processed CTD data or None if processing failed
    """
    if sbs_id is None:
        raise ImportError(
            "seabirdscientific is required for CTD processing. "
            "Install with: pip install seabirdscientific"
        )

    logger.info(f"Processing CTD cast: {cast.cast_id} from {cast.hex_file}")

    # Parse metadata from header file
    if cast.hdr_file and cast.hdr_file.exists():
        hdr_metadata = parse_hdr_file(cast.hdr_file)
        cast.latitude = hdr_metadata.get('latitude')
        cast.longitude = hdr_metadata.get('longitude')
        cast.start_time = hdr_metadata.get('start_time')
        cast.station = hdr_metadata.get('station')

    # Parse configuration from XMLCON
    xmlcon_config = {}
    if cast.xmlcon_file and cast.xmlcon_file.exists():
        xmlcon_config = parse_xmlcon_file(cast.xmlcon_file)
        cast.sensors = xmlcon_config.get('sensors', [])

    # Determine enabled sensors based on XMLCON
    enabled_sensors = [
        sbs_id.Sensors.Temperature,
        sbs_id.Sensors.Conductivity,
        sbs_id.Sensors.Pressure,
    ]

    # Check for secondary T/C
    if xmlcon_config.get('sensors'):
        sensor_types = [s.get('type', '') for s in xmlcon_config['sensors']]
        if 'TemperatureSensor' in sensor_types[3:]:
            enabled_sensors.append(sbs_id.Sensors.SecondaryTemperature)
        if 'ConductivitySensor' in sensor_types[4:]:
            enabled_sensors.append(sbs_id.Sensors.SecondaryConductivity)

    # Add voltage channels based on config
    voltage_count = 8 - xmlcon_config.get('voltage_words_suppressed', 0)
    for i in range(min(voltage_count, 8)):
        sensor_name = f'ExtVolt{i}'
        if hasattr(sbs_id.Sensors, sensor_name):
            enabled_sensors.append(getattr(sbs_id.Sensors, sensor_name))

    # Add NMEA location if configured
    if xmlcon_config.get('nmea_position_added', False):
        enabled_sensors.append(sbs_id.Sensors.nmeaLocation)

    # Add system time if configured
    if xmlcon_config.get('scan_time_added', False):
        enabled_sensors.append(sbs_id.Sensors.SystemTime)

    try:
        # Read hex file using seabirdscientific
        raw_data = sbs_id.read_hex_file(
            filepath=cast.hex_file,
            instrument_type=sbs_id.InstrumentType.SBE911Plus,
            enabled_sensors=enabled_sensors,
            frequency_channels_suppressed=xmlcon_config.get('frequency_channels_suppressed', 0),
            voltage_words_suppressed=xmlcon_config.get('voltage_words_suppressed', 0),
        )

        cast.raw_data = raw_data
        logger.info(f"Read {len(raw_data)} scans from {cast.hex_file.name}")

    except Exception as e:
        logger.error(f"Failed to read hex file {cast.hex_file}: {e}")
        return None

    # Build processed DataFrame with standard column names
    processed: dict[str, Any] = {}

    # Add cast metadata as columns
    processed['cast_id'] = cast.cast_id
    processed['cruise_id'] = cast.cruise_id
    if cast.station:
        processed['station'] = cast.station

    # Add position from header or NMEA
    if cast.latitude is not None:
        processed['latitude'] = cast.latitude
    elif 'NMEA Latitude' in raw_data.columns:
        processed['latitude'] = raw_data['NMEA Latitude']

    if cast.longitude is not None:
        processed['longitude'] = cast.longitude
    elif 'NMEA Longitude' in raw_data.columns:
        processed['longitude'] = raw_data['NMEA Longitude']

    # Add time
    if cast.start_time is not None:
        processed['start_time'] = cast.start_time.isoformat()
    if 'system time' in raw_data.columns:
        processed['time'] = raw_data['system time']

    # --- Calibrated conversion ---
    if calibrate and cast.sensors and sbs_conv is not None:
        lat = cast.latitude if cast.latitude is not None else 0.0
        try:
            cal = _calibrate_cast(raw_data, cast.sensors, lat)
            for col_name, values in cal.items():
                processed[col_name] = values
            logger.info("Calibrated %d columns: %s", len(cal), ", ".join(cal.keys()))
        except Exception as e:
            logger.warning("Calibrated conversion failed, falling back to raw: %s", e)

    # Always include raw frequency / voltage data
    column_mapping = {
        'temperature': 'temperature_freq',
        'conductivity': 'conductivity_freq',
        'digiquartz pressure': 'pressure_freq',
        'secondary temperature': 'temperature2_freq',
        'secondary conductivity': 'conductivity2_freq',
        'volt 0': 'volt0',
        'volt 1': 'volt1',
        'volt 2': 'volt2',
        'volt 3': 'volt3',
        'volt 4': 'volt4',
        'volt 5': 'volt5',
        'volt 6': 'volt6',
        'volt 7': 'volt7',
        'surface par': 'surface_par',
        'SBE911 pump status': 'pump_status',
    }

    for raw_col, new_col in column_mapping.items():
        if raw_col in raw_data.columns:
            processed[new_col] = raw_data[raw_col]

    # Create processed DataFrame
    processed_df = pd.DataFrame(processed)

    # Add scan number as index
    processed_df['scan'] = range(1, len(processed_df) + 1)

    cast.processed_data = processed_df

    # Write output CSV if requested
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{cast.cruise_id}_{cast.cast_id}.csv"
        processed_df.to_csv(output_file, index=False)
        logger.info(f"Wrote {len(processed_df)} rows to {output_file}")

    return processed_df


def ctd_descriptor_processor(
    data_dir: Path,
    file_info: R2RFileInfo,
    sensor_info: R2RSensorInfo,
    provider_id: str,
) -> SensorDescriptor:
    """Build a SensorDescriptor for an R2R CTD instrument.

    This is called by the R2R provider's inspect_archives method to
    catalog CTD sensors found in R2R archives.
    """
    from oceanstream.sensors.processor_base import SensorDescriptor

    campaign_id = file_info.campaign_id or "unknown_campaign"
    platform_id = file_info.platform

    metadata: dict[str, str] = {}
    metadata.update(file_info.extra or {})
    metadata.update(sensor_info.extra or {})

    if sensor_info.sensor_id:
        metadata.setdefault("instrument_id", sensor_info.sensor_id)
    if sensor_info.description:
        metadata.setdefault("instrument_description", sensor_info.description)

    # Count casts in data directory
    casts = find_cast_files(data_dir)
    if casts:
        metadata["cast_count"] = str(len(casts))
        metadata["cast_ids"] = ",".join(c.cast_id for c in casts[:10])  # First 10

    return SensorDescriptor(
        sensor_type=sensor_info.sensor_type or SENSOR_TYPE_CTD,
        sensor_id=SENSOR_ID_CTD,
        provider_id=provider_id,
        platform_id=platform_id,
        campaign_id=campaign_id,
        description=sensor_info.description or "SeaBird SBE-911+ CTD",
        metadata=metadata,
    )


def ctd_raw_processor(
    data_dir: Path,
    file_info: R2RFileInfo,
    sensor_info: R2RSensorInfo,
    descriptor: SensorDescriptor,
) -> Path:
    """Process R2R CTD raw data into CSV files.

    Args:
        data_dir: Directory containing .hex, .hdr, .xmlcon files
        file_info: R2R file metadata
        sensor_info: R2R sensor metadata
        descriptor: Sensor descriptor from catalog

    Returns:
        Path to output directory containing processed CSV files
    """
    if sbs_id is None:
        logger.warning(
            "seabirdscientific not installed, CTD processing unavailable. "
            "Install with: pip install seabirdscientific"
        )
        return data_dir

    # Find and process all casts
    casts = find_cast_files(data_dir)

    if not casts:
        logger.warning(f"No CTD hex files found in {data_dir}")
        return data_dir

    logger.info(f"Found {len(casts)} CTD casts to process")

    # Create output directory
    output_dir = data_dir / "processed"

    processed_count = 0
    for cast in casts:
        try:
            result = process_ctd_cast(cast, output_dir)
            if result is not None:
                processed_count += 1
        except Exception as e:
            logger.error(f"Failed to process cast {cast.cast_id}: {e}")

    logger.info(f"Processed {processed_count}/{len(casts)} CTD casts")

    return output_dir


def process_ctd(
    input_path: Path | str,
    output_dir: Path | str | None = None,
    work_dir: Path | str | None = None,
    cleanup: bool = True,
) -> pd.DataFrame | None:
    """Process CTD data from archive, directory, or individual file.

    This is the main entry point for CTD processing. It accepts:
    - R2R .tar.gz archive containing CTD data
    - Directory containing .hex, .hdr, .xmlcon files
    - Single .hex file (will find associated files in same directory)

    Args:
        input_path: Path to archive, directory, or .hex file
        output_dir: Directory to write processed CSV files. If None, creates
                    'processed' subdirectory in data location.
        work_dir: Directory for extracting archives. If None, uses temp dir.
        cleanup: If True, removes temp extraction directory after processing

    Returns:
        DataFrame with all processed casts combined, or None if no casts found

    Examples:
        # Process R2R archive
        df = process_ctd('/data/RR2402_160202_ctd.tar.gz')

        # Process extracted directory
        df = process_ctd('/tmp/RR2402/160202/data')

        # Process single hex file
        df = process_ctd('/data/RR2402_cast10.hex')

        # Process with custom output
        df = process_ctd(
            '/data/RR2402_160202_ctd.tar.gz',
            output_dir='/output/ctd_processed',
            work_dir='/tmp/work'
        )
    """
    if sbs_id is None:
        raise ImportError(
            "seabirdscientific is required for CTD processing. "
            "Install with: pip install seabirdscientific"
        )

    input_path = Path(input_path)
    output_path = Path(output_dir) if output_dir else None
    work_path = Path(work_dir) if work_dir else None

    # Create input handler
    ctd_input = CTDInput(path=input_path)

    try:
        # Extract if archive
        data_dir = ctd_input.extract(work_path)

        # Find all casts
        if ctd_input.is_single_file:
            # Process single file - find associated files
            hex_file = input_path
            casts = find_cast_files(hex_file.parent)
            # Filter to just the requested file
            cast_name = hex_file.stem.split('_')[-1].lower()
            casts = [c for c in casts if c.cast_id == cast_name]
            if not casts:
                # Create cast for just this file
                parts = hex_file.stem.split('_')
                cruise_id = parts[0] if len(parts) > 1 else 'unknown'
                cast_id = parts[1] if len(parts) > 1 else hex_file.stem
                casts = [
                    CTDCast(
                        cast_id=cast_id.lower(),
                        cruise_id=cruise_id,
                        hex_file=hex_file,
                    )
                ]
        else:
            casts = find_cast_files(data_dir)

        if not casts:
            logger.warning(f"No CTD hex files found in {data_dir}")
            return None

        logger.info(f"Found {len(casts)} CTD casts to process")

        # Determine output directory
        if output_path is None:
            output_path = data_dir / "processed"
        output_path.mkdir(parents=True, exist_ok=True)

        # Process all casts
        all_dfs = []
        for cast in casts:
            try:
                df = process_ctd_cast(cast, output_path)
                if df is not None:
                    all_dfs.append(df)
            except Exception as e:
                logger.error(f"Failed to process cast {cast.cast_id}: {e}")

        logger.info(f"Processed {len(all_dfs)}/{len(casts)} CTD casts")

        if not all_dfs:
            return None

        # Combine all casts
        combined = pd.concat(all_dfs, ignore_index=True)

        # Write combined output
        combined_file = output_path / "all_casts.csv"
        combined.to_csv(combined_file, index=False)
        logger.info(f"Wrote combined output ({len(combined)} rows) to {combined_file}")

        return combined

    finally:
        if cleanup:
            ctd_input.cleanup()


def process_ctd_archive(
    archive_path: Path | str,
    output_dir: Path | str | None = None,
    work_dir: Path | str | None = None,
) -> pd.DataFrame | None:
    """Process CTD data from an R2R .tar.gz archive.

    Convenience function for processing archives.
    See process_ctd() for full documentation.
    """
    return process_ctd(archive_path, output_dir, work_dir, cleanup=True)


# ---------------------------------------------------------------------------
# CNV file parser (for loading SeaBird-processed ground truth)
# ---------------------------------------------------------------------------


def parse_cnv_file(cnv_path: Path | str) -> pd.DataFrame:
    """Parse a SeaBird .cnv file into a DataFrame.

    The CNV format has a ``#``/``*`` prefixed header followed by
    whitespace-delimited data rows.  Column names are extracted from
    ``# name N = <short_name>: ...`` lines.

    Parameters
    ----------
    cnv_path : Path | str
        Path to the ``.cnv`` file.

    Returns
    -------
    pd.DataFrame
        DataFrame with one column per CNV variable, using the short
        names (e.g. ``t090C``, ``c0S/m``, ``sal00``, ``prDM``).
    """
    cnv_path = Path(cnv_path)
    columns: list[str] = []
    data_lines: list[str] = []
    in_data = False

    with open(cnv_path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if in_data:
                stripped = line.strip()
                if stripped:
                    data_lines.append(stripped)
                continue
            if line.startswith('*END*'):
                in_data = True
                continue
            # Parse column name lines: "# name 0 = t090C: Temperature ..."
            match = re.match(r'#\s*name\s+\d+\s*=\s*(\S+)', line)
            if match:
                columns.append(match.group(1).rstrip(':'))

    if not columns or not data_lines:
        raise ValueError(f"Could not parse CNV file: {cnv_path}")

    # Parse whitespace-delimited data
    rows = [line.split() for line in data_lines]
    df = pd.DataFrame(rows, columns=columns[: len(rows[0])])
    # Convert all columns to float where possible
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


# Register processors
try:
    from oceanstream.sensors.processors import (
        register_raw_processor,
        register_sensor_processor,
    )

    register_sensor_processor(SENSOR_TYPE_CTD, ctd_descriptor_processor)
    register_raw_processor(SENSOR_TYPE_CTD, ctd_raw_processor)
except ImportError:
    # Processors module not available during import
    pass
