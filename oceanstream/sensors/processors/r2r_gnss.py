"""R2R GNSS/Navigation sensor processor.

Handles R2R trackline navigation data (r2rnav) from various GNSS receivers
used on research vessels. This includes best resolution, 1-minute, and control
trackline data products.
"""

from oceanstream.sensors.detector import SensorDescriptor


def detect_r2r_gnss(columns: list[str], metadata: dict) -> SensorDescriptor | None:
    """Detect R2R GNSS/Navigation sensor from columns and metadata.
    
    R2R trackline navigation data typically includes:
    - Position: longitude, latitude
    - GPS quality: gps_quality (NMEA quality indicator), num_satellites, horizontal_dilution (HDOP)
    - Antenna: gps_antenna_height
    - Movement: speed_over_ground, course_over_ground
    
    Args:
        columns: List of column names from the dataset
        metadata: GeoCSV metadata dictionary
        
    Returns:
        SensorDescriptor if R2R GNSS data is detected, None otherwise
    """
    # Check for R2R GNSS-specific columns
    gnss_indicators = {
        'gps_quality',           # NMEA quality indicator
        'num_satellites',        # Number of satellites
        'horizontal_dilution',   # HDOP
        'gps_antenna_height',    # Antenna height above MSL
    }
    
    # Must have at least GPS quality indicators to be considered GNSS data
    if not any(ind in columns for ind in ['gps_quality', 'nmea_quality']):
        return None
    
    # Check if we have navigation-related columns
    nav_columns = {'speed_over_ground', 'course_over_ground', 'speed_made_good', 'course_made_good'}
    has_nav = any(col in columns for col in nav_columns)
    
    # Count how many GNSS indicators we have
    matched_indicators = sum(1 for ind in gnss_indicators if ind in columns)
    
    # Require at least 2 GNSS indicators to confidently identify as GNSS
    if matched_indicators < 2:
        return None
    
    # Extract device information from metadata if available
    device_model = metadata.get('R2R-ParentDeviceModel', 'Unknown GNSS')
    device_type = metadata.get('R2R-ParentDeviceType', 'gnss')
    
    # Clean up manufacturer from model string (e.g., "com.furuno GP-170" -> "Furuno")
    manufacturer = "Unknown"
    if device_model != 'Unknown GNSS':
        parts = device_model.split()
        if parts:
            manufacturer_part = parts[0].replace('com.', '').replace('edu.', '')
            manufacturer = manufacturer_part.capitalize()
    
    # Determine variables present in this dataset
    variables = []
    standard_mapping = {
        'longitude': 'longitude',
        'latitude': 'latitude',
        'ship_longitude': 'longitude',
        'ship_latitude': 'latitude',
        'gps_quality': 'gps_quality',
        'nmea_quality': 'gps_quality',
        'num_satellites': 'num_satellites',
        'nsv': 'num_satellites',
        'horizontal_dilution': 'horizontal_dilution',
        'hdop': 'horizontal_dilution',
        'gps_antenna_height': 'gps_antenna_height',
        'antenna_height': 'gps_antenna_height',
        'speed_over_ground': 'speed_over_ground',
        'speed_made_good': 'speed_over_ground',
        'course_over_ground': 'course_over_ground',
        'course_made_good': 'course_over_ground',
    }
    
    # Map columns to standard variable names
    seen_vars = set()
    for col in columns:
        if col in standard_mapping:
            std_var = standard_mapping[col]
            if std_var not in seen_vars:
                variables.append(std_var)
                seen_vars.add(std_var)
    
    return SensorDescriptor(
        sensor_id="gnss-navigation",
        name="GNSS Navigation Receiver",
        sensor_type="navigation",
        manufacturer=manufacturer,
        model=device_model if device_model != 'Unknown GNSS' else "Various",
        description=f"GNSS receiver providing position, velocity, and quality metrics. "
                   f"Device: {device_type}",
        variables=variables,
        mount_position="vessel superstructure",
        specifications={
            "system_type": "GNSS (GPS/GLONASS/Galileo/BeiDou)",
            "typical_accuracy": "2-10m horizontal",
            "update_rate": "1-10 Hz (depends on processing)",
            "output_format": "GeoCSV trackline",
        }
    )
