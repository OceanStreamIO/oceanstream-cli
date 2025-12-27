# Real-Time NMEA Streaming Processing

Complete guide for processing real-time NMEA data streams from live GNSS receivers, including serial port connections, network streams, and buffered ingestion.

## Overview

Real-time NMEA processing enables:

- **Live Ship Tracking**: Stream position data from vessel GNSS in real-time
- **USV/AUV Operations**: Monitor autonomous platform navigation during missions
- **Shore-Based Monitoring**: Track fleet positions via satellite/cellular links
- **Edge Computing**: Process GNSS data onboard for immediate decision-making
- **Data Logging**: Continuous archival of navigation data with automatic rotation

## Architecture Patterns

### Pattern 1: Stream → Buffer → Batch Processing

The recommended approach for production systems:

```
GNSS Device → Serial/Network → Buffer (memory/disk) → Batch Process → GeoParquet
   (1 Hz)         (live)           (every N seconds)    (periodic)      (archive)
```

**Advantages:**
- Reliable data capture (no packet loss)
- Efficient bulk processing
- Automatic recovery from processing failures
- Configurable batch sizes and intervals

### Pattern 2: Direct Stream Processing

For low-latency applications:

```
GNSS Device → Serial/Network → Parse & Process → Real-Time Output
   (1 Hz)         (live)          (immediate)      (streaming API)
```

**Advantages:**
- Minimal latency (< 1 second)
- Suitable for real-time dashboards
- Lower memory footprint

**Disadvantages:**
- No buffering (data loss on errors)
- Higher processing overhead per point

## Prerequisites

### Python Packages

```bash
# Install OceanStream with NMEA support
pip install oceanstream[geotrack]

# Additional packages for serial/network streaming
pip install pyserial      # Serial port access
pip install asyncio-mqtt  # MQTT streaming (optional)
```

### Hardware/Network Requirements

- **Serial Connection**: USB-to-serial adapter or direct RS-232 connection
- **Network Connection**: TCP/UDP socket or MQTT broker access
- **GNSS Receiver**: Configured to output NMEA 0183 sentences (typically at 1 Hz)

## Pattern 1: Buffered Batch Processing

### Basic Buffered Stream

Capture NMEA stream to file, then process periodically:

```python
import serial
import time
from pathlib import Path
from datetime import datetime
from oceanstream.sensors.processors.nmea_gnss import process_nmea_raw

# Configuration
SERIAL_PORT = "/dev/ttyUSB0"  # Linux/Mac
# SERIAL_PORT = "COM3"         # Windows
BAUD_RATE = 4800               # Standard NMEA baud rate
BUFFER_FILE = Path("buffer/nmea_stream.txt")
BATCH_INTERVAL = 60            # Process every 60 seconds
OUTPUT_DIR = Path("output/realtime")

# Ensure directories exist
BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def capture_nmea_stream(port: str, output_file: Path, duration: int = 60):
    """Capture NMEA stream to file for specified duration."""
    print(f"Capturing NMEA from {port} for {duration} seconds...")
    
    with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
        with open(output_file, 'a') as f:
            start_time = time.time()
            line_count = 0
            
            while (time.time() - start_time) < duration:
                try:
                    line = ser.readline().decode('ascii', errors='ignore').strip()
                    if line.startswith('$'):
                        # Add timestamp prefix
                        timestamp = datetime.utcnow().isoformat() + 'Z'
                        f.write(f"{timestamp} {line}\n")
                        f.flush()  # Ensure data is written immediately
                        line_count += 1
                        
                        if line_count % 100 == 0:
                            print(f"  Captured {line_count} sentences...")
                            
                except Exception as e:
                    print(f"  Error reading line: {e}")
                    continue
    
    print(f"✓ Captured {line_count} NMEA sentences")
    return line_count

def process_buffer(buffer_file: Path, output_dir: Path, campaign_id: str):
    """Process buffered NMEA data to GeoParquet."""
    if not buffer_file.exists() or buffer_file.stat().st_size == 0:
        print("No data to process")
        return
    
    print(f"Processing buffer: {buffer_file}")
    
    # Convert NMEA to CSV
    csv_file = buffer_file.with_suffix('.csv')
    stats = process_nmea_raw(buffer_file, csv_file, sampling_interval=1.0)
    
    print(f"  ✓ Processed {stats['data_points_written']} points")
    
    # Process to GeoParquet using OceanStream
    from oceanstream.geotrack.processor import convert
    from oceanstream.providers import get_provider
    
    provider = get_provider("generic")
    convert(
        provider=provider,
        input_source=csv_file,
        output_dir=output_dir,
        campaign_id=campaign_id,
        verbose=False,
        yes=True
    )
    
    print(f"  ✓ Written to {output_dir / campaign_id}")
    
    # Archive or delete buffer
    archive_file = buffer_file.parent / f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    buffer_file.rename(archive_file)
    csv_file.unlink(missing_ok=True)
    print(f"  ✓ Archived buffer to {archive_file.name}")

# Main loop: capture and process
campaign_id = f"realtime_{datetime.now().strftime('%Y%m%d')}"

while True:
    try:
        # Capture for 60 seconds
        capture_nmea_stream(SERIAL_PORT, BUFFER_FILE, duration=BATCH_INTERVAL)
        
        # Process accumulated data
        process_buffer(BUFFER_FILE, OUTPUT_DIR, campaign_id)
        
        print(f"Waiting {BATCH_INTERVAL} seconds before next batch...\n")
        time.sleep(BATCH_INTERVAL)
        
    except KeyboardInterrupt:
        print("\nStopping stream capture...")
        # Final processing
        if BUFFER_FILE.exists():
            process_buffer(BUFFER_FILE, OUTPUT_DIR, campaign_id)
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(10)  # Wait before retry
```

### With Rotating Buffers

For continuous long-term operation:

```python
import serial
import time
from pathlib import Path
from datetime import datetime
from collections import deque

class RotatingNMEABuffer:
    """Rotating buffer for NMEA data with automatic processing."""
    
    def __init__(self, buffer_dir: Path, max_buffers: int = 10, buffer_duration: int = 300):
        self.buffer_dir = buffer_dir
        self.max_buffers = max_buffers
        self.buffer_duration = buffer_duration
        self.current_buffer = None
        self.buffer_start = None
        self.buffers = deque(maxlen=max_buffers)
        
        buffer_dir.mkdir(parents=True, exist_ok=True)
    
    def get_current_buffer(self) -> Path:
        """Get or create current buffer file."""
        now = time.time()
        
        if self.current_buffer is None or (now - self.buffer_start) > self.buffer_duration:
            # Create new buffer
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            self.current_buffer = self.buffer_dir / f"nmea_{timestamp}.txt"
            self.buffer_start = now
            self.buffers.append(self.current_buffer)
            print(f"Started new buffer: {self.current_buffer.name}")
        
        return self.current_buffer
    
    def write_line(self, line: str):
        """Write NMEA line to current buffer."""
        buffer_file = self.get_current_buffer()
        with open(buffer_file, 'a') as f:
            timestamp = datetime.utcnow().isoformat() + 'Z'
            f.write(f"{timestamp} {line}\n")
    
    def get_ready_buffers(self) -> list[Path]:
        """Get completed buffers ready for processing."""
        if len(self.buffers) < 2:
            return []
        # Return all except the current (last) buffer
        return list(self.buffers)[:-1]
    
    def remove_buffer(self, buffer_file: Path):
        """Remove a buffer from tracking."""
        if buffer_file in self.buffers:
            self.buffers.remove(buffer_file)

# Usage
buffer = RotatingNMEABuffer(Path("buffers"), max_buffers=20, buffer_duration=300)  # 5 min buffers

def stream_with_rotation(port: str, output_dir: Path, campaign_id: str):
    """Stream NMEA with rotating buffers and background processing."""
    with serial.Serial(port, 4800, timeout=1) as ser:
        sentence_count = 0
        last_process_time = time.time()
        PROCESS_INTERVAL = 60  # Process every minute
        
        while True:
            try:
                line = ser.readline().decode('ascii', errors='ignore').strip()
                if line.startswith('$'):
                    buffer.write_line(line)
                    sentence_count += 1
                    
                    if sentence_count % 1000 == 0:
                        print(f"Captured {sentence_count} sentences...")
                
                # Periodically process ready buffers
                if (time.time() - last_process_time) > PROCESS_INTERVAL:
                    ready_buffers = buffer.get_ready_buffers()
                    for buf in ready_buffers:
                        print(f"\nProcessing buffer: {buf.name}")
                        csv_file = buf.with_suffix('.csv')
                        
                        # Convert to CSV
                        from oceanstream.sensors.processors.nmea_gnss import process_nmea_raw
                        stats = process_nmea_raw(buf, csv_file, sampling_interval=1.0)
                        
                        # Process to GeoParquet
                        from oceanstream.geotrack.processor import convert
                        from oceanstream.providers import get_provider
                        provider = get_provider("generic")
                        convert(provider, csv_file, output_dir, campaign_id, verbose=False, yes=True)
                        
                        # Clean up
                        buf.unlink()
                        csv_file.unlink(missing_ok=True)
                        buffer.remove_buffer(buf)
                        print(f"  ✓ Processed and archived")
                    
                    last_process_time = time.time()
                    
            except KeyboardInterrupt:
                print("\nStopping stream...")
                break
            except Exception as e:
                print(f"Error: {e}")
                continue

# Run streaming processor
stream_with_rotation("/dev/ttyUSB0", Path("output/stream"), "vessel_realtime")
```

## Pattern 2: Direct Stream Processing

### Line-by-Line Processing

For immediate processing without buffering:

```python
import serial
from datetime import datetime
from pathlib import Path
from oceanstream.sensors.processors.nmea_gnss import parse_nmea_line
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

class RealtimeNMEAProcessor:
    """Process NMEA sentences in real-time."""
    
    def __init__(self, output_file: Path, flush_interval: int = 10):
        self.output_file = output_file
        self.flush_interval = flush_interval  # Flush to disk every N points
        self.buffer = []
        self.point_count = 0
        
        # Initialize output file with headers
        if not output_file.exists():
            df = pd.DataFrame(columns=[
                'time', 'latitude', 'longitude', 'altitude',
                'fix_quality', 'num_satellites', 'hdop',
                'speed_knots', 'course'
            ])
            df.to_csv(output_file, index=False)
    
    def process_sentence(self, nmea_sentence: str) -> dict | None:
        """Process a single NMEA sentence."""
        timestamp = datetime.utcnow().isoformat() + 'Z'
        line = f"{timestamp} {nmea_sentence}"
        
        # Parse using OceanStream parser
        data = parse_nmea_line(line)
        
        if data and 'latitude' in data and 'longitude' in data:
            return {
                'time': data['timestamp'],
                'latitude': data.get('latitude'),
                'longitude': data.get('longitude'),
                'altitude': data.get('gps_antenna_height'),
                'fix_quality': data.get('gps_quality'),
                'num_satellites': data.get('num_satellites'),
                'hdop': data.get('horizontal_dilution'),
                'speed_knots': data.get('speed_over_ground', 0) / 0.514444,  # m/s to knots
                'course': data.get('course_over_ground')
            }
        return None
    
    def add_point(self, data: dict):
        """Add a processed point to buffer."""
        self.buffer.append(data)
        self.point_count += 1
        
        if len(self.buffer) >= self.flush_interval:
            self.flush()
    
    def flush(self):
        """Flush buffer to disk."""
        if not self.buffer:
            return
        
        df = pd.DataFrame(self.buffer)
        df.to_csv(self.output_file, mode='a', header=False, index=False)
        print(f"  ✓ Flushed {len(self.buffer)} points to disk (total: {self.point_count})")
        self.buffer = []
    
    def close(self):
        """Final flush and cleanup."""
        self.flush()
        print(f"✓ Processing complete: {self.point_count} total points")

# Usage
def stream_realtime(port: str, output_file: Path, duration: int = 300):
    """Stream and process NMEA in real-time."""
    processor = RealtimeNMEAProcessor(output_file, flush_interval=10)
    
    print(f"Starting real-time NMEA processing from {port}...")
    print(f"Output: {output_file}")
    print(f"Duration: {duration} seconds\n")
    
    with serial.Serial(port, 4800, timeout=1) as ser:
        start_time = time.time()
        
        while (time.time() - start_time) < duration:
            try:
                line = ser.readline().decode('ascii', errors='ignore').strip()
                if line.startswith('$'):
                    data = processor.process_sentence(line)
                    if data:
                        processor.add_point(data)
                        
            except KeyboardInterrupt:
                print("\nStopping...")
                break
            except Exception as e:
                print(f"Error: {e}")
                continue
    
    processor.close()
    print(f"\n✓ Data saved to {output_file}")

# Run for 5 minutes
stream_realtime("/dev/ttyUSB0", Path("output/realtime.csv"), duration=300)
```

## Network Streaming

### TCP Socket Streaming

For GNSS receivers with network output:

```python
import socket
from datetime import datetime
from pathlib import Path

def stream_from_tcp(host: str, port: int, output_file: Path, duration: int = 300):
    """Stream NMEA from TCP socket."""
    print(f"Connecting to {host}:{port}...")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    sock.settimeout(5.0)
    
    print(f"Connected. Streaming to {output_file}...")
    
    with open(output_file, 'w') as f:
        start_time = time.time()
        sentence_count = 0
        
        while (time.time() - start_time) < duration:
            try:
                data = sock.recv(1024).decode('ascii', errors='ignore')
                for line in data.split('\n'):
                    line = line.strip()
                    if line.startswith('$'):
                        timestamp = datetime.utcnow().isoformat() + 'Z'
                        f.write(f"{timestamp} {line}\n")
                        sentence_count += 1
                        
                        if sentence_count % 100 == 0:
                            print(f"  Received {sentence_count} sentences...")
                            
            except socket.timeout:
                print("  Socket timeout, retrying...")
                continue
            except KeyboardInterrupt:
                print("\nStopping...")
                break
            except Exception as e:
                print(f"Error: {e}")
                break
    
    sock.close()
    print(f"✓ Captured {sentence_count} sentences")

# Usage
stream_from_tcp("192.168.1.100", 2947, Path("output/tcp_stream.txt"), duration=600)
```

### UDP Multicast Streaming

For ship network broadcasts:

```python
import socket
import struct

def stream_from_udp_multicast(multicast_group: str, port: int, output_file: Path):
    """Stream NMEA from UDP multicast."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', port))
    
    # Join multicast group
    mreq = struct.pack("4sl", socket.inet_aton(multicast_group), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    
    print(f"Listening on {multicast_group}:{port}...")
    
    with open(output_file, 'w') as f:
        try:
            while True:
                data, addr = sock.recvfrom(1024)
                line = data.decode('ascii', errors='ignore').strip()
                if line.startswith('$'):
                    timestamp = datetime.utcnow().isoformat() + 'Z'
                    f.write(f"{timestamp} {line}\n")
                    f.flush()
        except KeyboardInterrupt:
            print("\nStopping...")
    
    sock.close()

# Usage (common maritime multicast group)
stream_from_udp_multicast("239.192.0.1", 10110, Path("output/udp_stream.txt"))
```

## Production Deployment

### Systemd Service (Linux)

Create `/etc/systemd/system/nmea-stream.service`:

```ini
[Unit]
Description=NMEA Real-Time Stream Processor
After=network.target

[Service]
Type=simple
User=oceanstream
WorkingDirectory=/opt/oceanstream
ExecStart=/opt/oceanstream/venv/bin/python /opt/oceanstream/stream_processor.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable nmea-stream
sudo systemctl start nmea-stream
sudo systemctl status nmea-stream
```

### Docker Container

`Dockerfile`:
```dockerfile
FROM python:3.12-slim

RUN pip install oceanstream[geotrack] pyserial

WORKDIR /app
COPY stream_processor.py .

# For serial device access
RUN usermod -a -G dialout root

CMD ["python", "stream_processor.py"]
```

`docker-compose.yml`:
```yaml
version: '3.8'
services:
  nmea-stream:
    build: .
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
    volumes:
      - ./output:/app/output
      - ./buffers:/app/buffers
    restart: unless-stopped
    environment:
      - SERIAL_PORT=/dev/ttyUSB0
      - OUTPUT_DIR=/app/output
      - BUFFER_DIR=/app/buffers
```

## Monitoring & Diagnostics

### Stream Health Check

```python
import time
from datetime import datetime, timedelta

class StreamMonitor:
    """Monitor NMEA stream health."""
    
    def __init__(self, alert_threshold: int = 30):
        self.alert_threshold = alert_threshold  # Seconds without data
        self.last_sentence_time = None
        self.total_sentences = 0
        self.error_count = 0
        self.start_time = datetime.now()
    
    def update(self, success: bool = True):
        """Update monitor with new sentence result."""
        if success:
            self.last_sentence_time = datetime.now()
            self.total_sentences += 1
        else:
            self.error_count += 1
    
    def check_health(self) -> dict:
        """Check stream health and return status."""
        now = datetime.now()
        
        if self.last_sentence_time is None:
            return {
                'status': 'no_data',
                'message': 'No data received yet'
            }
        
        time_since_last = (now - self.last_sentence_time).total_seconds()
        
        if time_since_last > self.alert_threshold:
            return {
                'status': 'stale',
                'message': f'No data for {time_since_last:.0f} seconds',
                'last_data': self.last_sentence_time.isoformat()
            }
        
        uptime = (now - self.start_time).total_seconds()
        rate = self.total_sentences / uptime if uptime > 0 else 0
        error_rate = self.error_count / self.total_sentences if self.total_sentences > 0 else 0
        
        return {
            'status': 'healthy',
            'uptime_seconds': uptime,
            'total_sentences': self.total_sentences,
            'rate_hz': rate,
            'error_count': self.error_count,
            'error_rate': error_rate,
            'last_data': self.last_sentence_time.isoformat()
        }
    
    def print_status(self):
        """Print current status."""
        health = self.check_health()
        print(f"\n=== Stream Monitor ===")
        print(f"Status: {health['status'].upper()}")
        if 'uptime_seconds' in health:
            print(f"Uptime: {health['uptime_seconds']:.0f}s")
            print(f"Sentences: {health['total_sentences']} ({health['rate_hz']:.2f} Hz)")
            print(f"Errors: {health['error_count']} ({health['error_rate']*100:.2f}%)")
            print(f"Last data: {health['last_data']}")
        else:
            print(f"Message: {health['message']}")
        print("=" * 22)

# Usage in stream processing
monitor = StreamMonitor(alert_threshold=30)

while True:
    try:
        line = ser.readline().decode('ascii', errors='ignore').strip()
        if line.startswith('$'):
            data = process_sentence(line)
            monitor.update(success=data is not None)
    except Exception as e:
        monitor.update(success=False)
        print(f"Error: {e}")
    
    # Print status every 60 seconds
    if monitor.total_sentences % 60 == 0:
        monitor.print_status()
```

## Troubleshooting

### No Data from Serial Port

**Problem**: Serial port opens but no data received.

**Solutions**:

1. Check device permissions:
   ```bash
   # Linux
   sudo usermod -a -G dialout $USER
   sudo chmod 666 /dev/ttyUSB0
   
   # Verify device exists
   ls -l /dev/ttyUSB*
   ```

2. Verify baud rate (common rates: 4800, 9600, 38400):
   ```python
   # Try different rates
   for rate in [4800, 9600, 38400]:
       print(f"Trying {rate}...")
       with serial.Serial('/dev/ttyUSB0', rate, timeout=2) as ser:
           line = ser.readline()
           if line:
               print(f"Success at {rate}: {line}")
               break
   ```

3. Check GNSS receiver output is enabled (consult device manual)

### High Memory Usage

**Problem**: Python process memory grows over time.

**Solutions**:

1. Flush buffers regularly:
   ```python
   if len(buffer) > 1000:
       flush_to_disk(buffer)
       buffer.clear()
   ```

2. Use rotating buffers instead of single large buffer

3. Process in separate subprocess:
   ```python
   from multiprocessing import Process
   
   def process_batch(file_path):
       # Processing in separate process
       pass
   
   p = Process(target=process_batch, args=(buffer_file,))
   p.start()
   p.join()  # Releases memory after completion
   ```

### Sentence Parse Errors

**Problem**: Many sentences fail to parse.

**Solutions**:

1. Check for checksums:
   ```python
   import pynmea2
   
   try:
       msg = pynmea2.parse(sentence)
   except pynmea2.ChecksumError:
       print("Checksum mismatch - data corruption?")
   except pynmea2.ParseError as e:
       print(f"Parse error: {e}")
   ```

2. Validate sentence format before parsing:
   ```python
   def is_valid_nmea(line: str) -> bool:
       return (
           line.startswith('$') and
           '*' in line and
           len(line) > 10 and
           line[-3] == '*'
       )
   ```

## Best Practices

### 1. Use Buffered Processing
Always use buffering for production systems to prevent data loss during processing failures.

### 2. Implement Health Monitoring
Monitor stream health and alert on stale data or high error rates.

### 3. Rotate Log Files
Use rotating buffers or log files to prevent disk space issues:
```python
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    'nmea_stream.log',
    maxBytes=100*1024*1024,  # 100 MB
    backupCount=10
)
```

### 4. Handle Device Reconnection
Implement automatic reconnection for network/serial failures:
```python
def connect_with_retry(port, max_attempts=5):
    for attempt in range(max_attempts):
        try:
            ser = serial.Serial(port, 4800, timeout=1)
            print(f"Connected to {port}")
            return ser
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
            time.sleep(5)
    raise ConnectionError(f"Failed to connect after {max_attempts} attempts")
```

### 5. Validate Coordinates
Check for invalid GPS coordinates (common during GPS acquisition):
```python
def is_valid_position(lat: float, lon: float) -> bool:
    return (
        -90 <= lat <= 90 and
        -180 <= lon <= 180 and
        (lat != 0 or lon != 0)  # Exclude null island
    )
```

## Performance Tuning

### Optimal Batch Sizes

| Data Rate | Buffer Duration | Batch Size | Processing Frequency |
|-----------|----------------|------------|---------------------|
| 1 Hz | 60s | ~60 points | Every minute |
| 5 Hz | 60s | ~300 points | Every minute |
| 10 Hz | 30s | ~300 points | Every 30 seconds |

### Memory Limits

Set maximum buffer sizes to prevent memory issues:
```python
MAX_BUFFER_SIZE = 10_000  # points
MAX_BUFFER_AGE = 300      # seconds

if len(buffer) > MAX_BUFFER_SIZE or (time.time() - buffer_start) > MAX_BUFFER_AGE:
    process_and_flush(buffer)
```

## See Also

- [NMEA Processing Guide](nmea-processing.md) - Batch file processing
- [Geotrack Convert Reference](../core-concepts/geotrack-convert-reference.md) - CLI options
- [Serial Port Programming](https://pythonhosted.org/pyserial/) - PySerial documentation
- [NMEA 0183 Standard](https://www.nmea.org/) - Official specification
