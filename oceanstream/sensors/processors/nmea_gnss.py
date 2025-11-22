"""NMEA GNSS raw data processor.

Parses NMEA 0183 sentences from GPS/GNSS receivers and converts them to CSV format
suitable for GeoParquet ingestion.

Supported NMEA sentences:
- GGA: GPS fix data (position, altitude, quality, satellites, HDOP)
- RMC: Recommended minimum (position, speed, course, date)
- GNS: GNSS fix data (multi-constellation position)
- VTG: Track and ground speed
- ZDA: Time and date (UTC time, essential for live streams)

Input format: <ISO8601_timestamp> <NMEA_sentence>
Example: 2024-02-17T00:00:00.110545Z $GPGGA,235959.00,3242.3912,N,11714.1643,W,1,10,0.8,10.4,M,-34.3,M,,*66

Note: For live streams without ISO8601 prefix, ZDA sentences provide authoritative GPS time.

Output CSV columns:
- time: ISO8601 timestamp
- latitude: Decimal degrees (-90 to 90)
- longitude: Decimal degrees (-180 to 180)
- gps_quality: NMEA quality indicator (0-9)
- num_satellites: Number of satellites used
- horizontal_dilution: HDOP value
- gps_antenna_height: Antenna height above mean sea level (meters)
- speed_over_ground: Speed in m/s
- course_over_ground: Course in degrees (0-360)
- gps_utc_time: UTC time from GPS (ISO8601, from ZDA if available)
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

try:
    import pynmea2
except ImportError:
    raise ImportError(
        "pynmea2 is required for NMEA processing. "
        "Install with: pip install oceanstream[geotrack]"
    )

if TYPE_CHECKING:
    from oceanstream.providers.r2r.r2r_metadata import R2RFileInfo, R2RSensorInfo
    from oceanstream.sensors.processor_base import SensorDescriptor

logger = logging.getLogger(__name__)

# Sensor ID for GNSS navigation (matches the sensor.json definition)
SENSOR_ID_GNSS = "gnss-navigation"


def parse_nmea_line(line: str) -> dict[str, Any] | None:
    """Parse a single NMEA line and extract relevant data.

    Args:
        line: Line in format "<ISO8601_timestamp> <NMEA_sentence>"

    Returns:
        Dictionary with parsed data or None if line cannot be parsed
    """
    line = line.strip()
    if not line:
        return None

    # Split timestamp and NMEA sentence
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        logger.debug(f"Skipping malformed line: {line}")
        return None

    timestamp_str, nmea_sentence = parts

    # Parse timestamp
    try:
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(f"Invalid timestamp format: {timestamp_str}")
        return None

    # Parse NMEA sentence
    try:
        msg = pynmea2.parse(nmea_sentence)
    except pynmea2.ParseError as e:
        logger.debug(f"Failed to parse NMEA sentence: {nmea_sentence} - {e}")
        return None

    # Extract data based on sentence type
    data: dict[str, Any] = {"timestamp": timestamp}

    if isinstance(msg, pynmea2.types.talker.GGA):
        # $GPGGA - GPS Fix Data
        # Note: pynmea2 already converts coordinates to decimal degrees
        if msg.latitude is not None and msg.longitude is not None:
            data["latitude"] = float(msg.latitude)
            data["longitude"] = float(msg.longitude)
        if msg.gps_qual is not None:
            data["gps_quality"] = int(msg.gps_qual)
        if msg.num_sats:
            data["num_satellites"] = int(msg.num_sats)
        if msg.horizontal_dil:
            data["horizontal_dilution"] = float(msg.horizontal_dil)
        if msg.altitude is not None:
            data["gps_antenna_height"] = float(msg.altitude)

    elif isinstance(msg, pynmea2.types.talker.RMC):
        # $GPRMC - Recommended Minimum
        if msg.latitude is not None and msg.longitude is not None:
            data["latitude"] = float(msg.latitude)
            data["longitude"] = float(msg.longitude)
        if msg.spd_over_grnd is not None:
            # Convert knots to m/s
            data["speed_over_ground"] = float(msg.spd_over_grnd) * 0.514444
        if msg.true_course is not None:
            data["course_over_ground"] = float(msg.true_course)

    elif isinstance(msg, pynmea2.types.talker.GNS):
        # $GPGNS - GNSS Fix Data
        if msg.latitude is not None and msg.longitude is not None:
            data["latitude"] = float(msg.latitude)
            data["longitude"] = float(msg.longitude)
        if msg.num_sats:
            data["num_satellites"] = int(msg.num_sats)
        if msg.hdop:
            data["horizontal_dilution"] = float(msg.hdop)
        if msg.altitude is not None:
            data["gps_antenna_height"] = float(msg.altitude)

    elif isinstance(msg, pynmea2.types.talker.VTG):
        # $GPVTG - Track and Ground Speed
        if msg.spd_over_grnd_kts is not None:
            # Convert knots to m/s
            data["speed_over_ground"] = float(msg.spd_over_grnd_kts) * 0.514444
        if msg.true_track is not None:
            data["course_over_ground"] = float(msg.true_track)

    elif isinstance(msg, pynmea2.types.talker.ZDA):
        # $GPZDA - Time and Date
        # Provides authoritative GPS UTC time, critical for live streams
        if hasattr(msg, 'timestamp') and msg.timestamp:
            # Combine date and time into ISO8601 format
            try:
                # ZDA provides: UTC time, day, month, year, local zone hours, local zone minutes
                utc_time = msg.timestamp  # datetime.time object
                day = int(msg.day) if msg.day else 1
                month = int(msg.month) if msg.month else 1
                year = int(msg.year) if msg.year else 1970
                
                # Create datetime from ZDA components
                gps_datetime = datetime(
                    year=year,
                    month=month,
                    day=day,
                    hour=utc_time.hour,
                    minute=utc_time.minute,
                    second=utc_time.second,
                    microsecond=utc_time.microsecond,
                    tzinfo=utc_time.tzinfo
                )
                data["gps_utc_time"] = gps_datetime.isoformat()
            except (ValueError, AttributeError) as e:
                logger.debug(f"Could not parse ZDA time/date: {e}")

    # Only return data if we extracted something useful
    if len(data) > 1:  # More than just timestamp
        return data
    return None


def _apply_sampling(data: list[dict[str, Any]], interval: float) -> list[dict[str, Any]]:
    """Apply time-based sampling/decimation to data points.
    
    Keeps one data point per sampling interval. For each interval,
    selects the point closest to the interval center.
    
    Args:
        data: List of data dictionaries with 'time' key (ISO8601 string)
        interval: Sampling interval in seconds
        
    Returns:
        Decimated list of data points
    """
    if not data or interval <= 0:
        return data
    
    sampled = []
    current_bucket_start = None
    bucket_points = []
    
    for point in data:
        timestamp = datetime.fromisoformat(point["time"])
        
        # Initialize first bucket
        if current_bucket_start is None:
            current_bucket_start = timestamp
            bucket_points = [point]
            continue
        
        # Calculate time since bucket start
        elapsed = (timestamp - current_bucket_start).total_seconds()
        
        if elapsed < interval:
            # Still in current bucket
            bucket_points.append(point)
        else:
            # Bucket complete - select middle point
            if bucket_points:
                mid_idx = len(bucket_points) // 2
                sampled.append(bucket_points[mid_idx])
            
            # Start new bucket
            current_bucket_start = timestamp
            bucket_points = [point]
    
    # Don't forget last bucket
    if bucket_points:
        mid_idx = len(bucket_points) // 2
        sampled.append(bucket_points[mid_idx])
    
    return sampled


def process_nmea_raw(
    input_path: Path,
    output_path: Path,
    sentence_types: list[str] | None = None,
    sampling_interval: float | None = None,
) -> dict[str, Any]:
    """Process NMEA raw data file and convert to CSV.

    Args:
        input_path: Path to NMEA raw data file
        output_path: Path for output CSV file
        sentence_types: List of sentence types to process (e.g., ['GGA', 'RMC']).
                       If None, processes all supported types.
        sampling_interval: Time interval in seconds for sampling/decimation.
                          If None, keeps all data points.
                          If specified, only keeps one point per interval.
                          Examples: 1.0 = 1 point/second, 10.0 = 1 point/10 seconds

    Returns:
        Dictionary with processing statistics including decimation info
    """
    if sentence_types is None:
        sentence_types = ["GGA", "RMC", "GNS", "VTG", "ZDA"]

    logger.info(f"Processing NMEA raw data: {input_path}")
    logger.info(f"Output CSV: {output_path}")
    logger.info(f"Processing sentence types: {', '.join(sentence_types)}")
    if sampling_interval:
        logger.info(f"Sampling interval: {sampling_interval}s (1 point per {sampling_interval}s)")

    # Collect data points
    data_points: list[dict[str, Any]] = []
    lines_read = 0
    lines_parsed = 0

    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            lines_read += 1
            parsed = parse_nmea_line(line)
            if parsed:
                lines_parsed += 1
                data_points.append(parsed)

    if not data_points:
        raise ValueError(f"No valid NMEA data found in {input_path}")

    # Merge data points by timestamp
    # (Multiple sentences can have same timestamp)
    merged_data: dict[datetime, dict[str, Any]] = {}
    for point in data_points:
        ts = point["timestamp"]
        if ts not in merged_data:
            merged_data[ts] = {"time": ts.isoformat()}

        # Merge fields (later values overwrite earlier ones)
        for key, value in point.items():
            if key != "timestamp":
                merged_data[ts][key] = value

    # Sort by timestamp
    sorted_data = sorted(merged_data.values(), key=lambda x: x["time"])

    # Apply sampling/decimation if requested
    pre_sampling_count = len(sorted_data)
    if sampling_interval and sampling_interval > 0:
        logger.info(f"Applying sampling: 1 point per {sampling_interval}s")
        sampled_data = _apply_sampling(sorted_data, sampling_interval)
        logger.info(f"Decimation: {pre_sampling_count:,} → {len(sampled_data):,} points "
                   f"({len(sampled_data)/pre_sampling_count*100:.1f}% retained)")
        sorted_data = sampled_data

    # Write CSV
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Define all possible columns
    all_columns = [
        "time",
        "latitude",
        "longitude",
        "gps_quality",
        "num_satellites",
        "horizontal_dilution",
        "gps_antenna_height",
        "speed_over_ground",
        "course_over_ground",
        "gps_utc_time",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted_data)

    stats = {
        "input_file": str(input_path),
        "output_file": str(output_path),
        "lines_read": lines_read,
        "lines_parsed": lines_parsed,
        "data_points_merged": pre_sampling_count,
        "data_points_written": len(sorted_data),
        "sampling_interval": sampling_interval,
        "decimation_ratio": len(sorted_data) / pre_sampling_count if pre_sampling_count > 0 else 1.0,
    }

    logger.info(f"Processed {lines_parsed}/{lines_read} lines")
    if sampling_interval:
        logger.info(f"Decimated {pre_sampling_count:,} → {len(sorted_data):,} points")
    logger.info(f"Wrote {len(sorted_data)} data points to {output_path}")

    return stats


def detect_nmea_gnss(file_path: Path) -> bool:
    """Detect if file contains NMEA GNSS data.

    Args:
        file_path: Path to file to check

    Returns:
        True if file appears to contain NMEA GNSS data
    """
    # Check file extension
    if file_path.suffix.lower() not in [".txt", ".nmea", ".log"]:
        return False

    # Check first few lines for NMEA sentences
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for _ in range(10):  # Check first 10 lines
                line = f.readline().strip()
                if not line:
                    continue

                # Look for NMEA sentence pattern: timestamp + $GP/GN sentence
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    sentence = parts[1]
                    if sentence.startswith("$GP") or sentence.startswith("$GN"):
                        # Try to parse it
                        try:
                            pynmea2.parse(sentence)
                            return True
                        except pynmea2.ParseError:
                            continue
    except Exception as e:
        logger.debug(f"Error checking NMEA format: {e}")
        return False

    return False


def nmea_raw_processor(
    data_dir: Path,
    file_info: R2RFileInfo,
    sensor_info: R2RSensorInfo,
    descriptor: SensorDescriptor,
) -> Path:
    """Process NMEA raw data files into CSV format.

    This is the raw processor interface expected by the R2R provider.
    It finds NMEA data files in the data directory, processes them,
    and writes a standardized CSV file.

    Args:
        data_dir: Directory containing raw NMEA data files
        file_info: R2R file metadata (not used for NMEA)
        sensor_info: R2R sensor metadata (not used for NMEA)
        descriptor: Sensor descriptor for this sensor

    Returns:
        Path to the generated CSV file
    """
    logger.info(f"Processing NMEA raw data in: {data_dir}")

    # Find NMEA data files (.txt, .nmea, .log)
    nmea_files = []
    for ext in [".txt", ".nmea", ".log"]:
        nmea_files.extend(sorted(data_dir.glob(f"*{ext}")))

    if not nmea_files:
        raise FileNotFoundError(f"No NMEA data files found in {data_dir}")

    # For now, process the first file found
    # TODO: Handle multiple files (concatenate or process separately?)
    input_file = nmea_files[0]
    logger.info(f"Processing NMEA file: {input_file}")

    # Output to gnss_navigation.csv in same directory
    output_file = data_dir / "gnss_navigation.csv"

    # Process the file
    stats = process_nmea_raw(input_file, output_file)

    logger.info(
        f"NMEA processing complete: {stats['data_points_written']} data points written"
    )

    return output_file


# Register the raw processor for GNSS navigation sensor (deferred to avoid circular imports)
def _register_nmea_processor() -> None:
    """Register NMEA processor (called from __init__.py after module load)."""
    from oceanstream.sensors.processors import register_raw_processor

    register_raw_processor(SENSOR_ID_GNSS, nmea_raw_processor)
