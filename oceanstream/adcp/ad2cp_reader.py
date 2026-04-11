"""Read Nortek Signature AD2CP binary files.

Pure-Python reader for ``.ad2cp`` files — no dolfyn dependency.
Extracts echosounder amplitude data, average velocity data, and
instrument configuration/calibration from the binary packet stream.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger("oceanstream")

# Nortek AD2CP packet IDs
_ID_STRING = 0xA0
_ID_BURST = 0x15
_ID_AVERAGE = 0x16
_ID_BOTTOM_TRACK = 0x17
_ID_BURST_ECHOSOUNDER = 0x1A
_ID_ECHOSOUNDER_RAW = 0x1B
_ID_ECHOSOUNDER = 0x1C

_SYNC_BYTE = 0xA5


@dataclass
class Ad2cpConfig:
    """Instrument configuration parsed from the string record."""

    serial_number: int = 0
    instrument_type: str = ""
    firmware_version: int = 0
    firmware_minor: int = 0
    frequencies: list[int] = field(default_factory=list)
    n_cells: int = 0
    cell_size: float = 0.0
    blanking: float = 0.0
    pulse_lengths: list[float] = field(default_factory=list)
    cal_offsets: list[float] = field(default_factory=list)
    beam_imp: dict[int, list[float]] = field(default_factory=dict)
    raw: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Ad2cpEchoPing:
    """A single echosounder ping."""

    time: np.datetime64
    sound_speed: float
    temperature: float
    pressure: float
    heading: float
    pitch: float
    roll: float
    amplitude: np.ndarray  # uint16 raw counts, shape (n_cells,)


@dataclass
class Ad2cpBottomTrack:
    """A single bottom-track detection."""

    time: np.datetime64
    pressure: float  # dbar (instrument depth)
    beam_distances_m: list[float]  # slant range per beam (m)
    beam_tilt_deg: float  # beam angle from vertical (°)


def _checksum(data: bytes) -> int:
    """Nortek AD2CP checksum: 0xB58C + sum of 16-bit LE words."""
    cs = 0xB58C
    for i in range(0, len(data) - 1, 2):
        cs += struct.unpack_from("<H", data, i)[0]
        cs &= 0xFFFF
    if len(data) % 2:
        cs += data[-1] << 8
        cs &= 0xFFFF
    return cs


def _parse_config(text: str) -> Ad2cpConfig:
    """Parse the string record text into an Ad2cpConfig."""
    cfg = Ad2cpConfig()
    cfg.raw = {}

    for line in text.split("\n"):
        line = line.strip("\r\n\x00 ")
        if not line or line.startswith("#"):
            continue
        # Format: COMMAND,KEY=VAL,KEY=VAL,...
        parts = line.split(",", 1)
        cmd = parts[0].strip()
        kv: dict[str, Any] = {}
        if len(parts) > 1:
            for token in parts[1].split(","):
                if "=" not in token:
                    continue
                k, v = token.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"')
                try:
                    kv[k] = int(v)
                except ValueError:
                    try:
                        kv[k] = float(v)
                    except ValueError:
                        kv[k] = v
        cfg.raw[cmd] = kv

        if cmd == "ID":
            cfg.instrument_type = kv.get("STR", "")
            cfg.serial_number = kv.get("SN", 0)
        elif cmd == "GETHW":
            cfg.firmware_version = kv.get("FW", 0)
            cfg.firmware_minor = kv.get("FWMINOR", 0)
        elif cmd == "GETECHO":
            cfg.n_cells = kv.get("NC", 0)
            cfg.cell_size = kv.get("BINSIZE", 0.0)
            cfg.blanking = kv.get("BD", 0.0)
            for i in range(1, 4):
                freq = kv.get(f"FREQ{i}", 0)
                if freq > 0:
                    cfg.frequencies.append(freq)
                    cfg.pulse_lengths.append(kv.get(f"XMIT{i}", 0.0))
        elif cmd == "CALECHOGET":
            for key in ("CHA0", "CHB0", "CHC0"):
                if key in kv:
                    cfg.cal_offsets.append(kv[key])
        elif cmd == "BEAMIMPLIST":
            beam_num = kv.get("BEAM", 0)
            coeffs = [kv.get(f"P{i}", 0.0) for i in range(5)]
            cfg.beam_imp[beam_num] = coeffs

    return cfg


def _parse_echo_data_record(data: bytes) -> Ad2cpEchoPing:
    """Parse a single echosounder data record payload."""
    offset_of_data = data[1]

    year = data[8] + 1900
    month = data[9] + 1
    day = data[10]
    hour = data[11]
    minute = data[12]
    sec = data[13]
    microsec100 = struct.unpack_from("<H", data, 14)[0]
    us = microsec100 * 100
    time = np.datetime64(
        f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{sec:02d}.{us:06d}"
    )

    sound_speed = struct.unpack_from("<H", data, 16)[0] * 0.1
    temperature = struct.unpack_from("<h", data, 18)[0] * 0.01
    pressure = struct.unpack_from("<I", data, 20)[0] * 0.001
    heading = struct.unpack_from("<H", data, 24)[0] * 0.01
    pitch = struct.unpack_from("<h", data, 26)[0] * 0.01
    roll = struct.unpack_from("<h", data, 28)[0] * 0.01

    echo_bytes = data[offset_of_data:]
    amplitude = np.frombuffer(echo_bytes, dtype="<u2")

    return Ad2cpEchoPing(
        time=time,
        sound_speed=sound_speed,
        temperature=temperature,
        pressure=pressure,
        heading=heading,
        pitch=pitch,
        roll=roll,
        amplitude=amplitude.copy(),
    )


def _parse_bt_data_record(
    data: bytes,
    beam_tilt_deg: float = 20.0,
) -> Ad2cpBottomTrack:
    """Parse a single bottom-track data record payload.

    The bottom-track record shares the common AD2CP header (version,
    offset_of_data, timestamp, etc.).  Per-beam distances start at
    ``offset_of_data + 24`` as int32 millimetres for 4 beams.
    """
    offset_of_data = data[1]

    year = data[8] + 1900
    month = data[9] + 1
    day = data[10]
    hour = data[11]
    minute = data[12]
    sec = data[13]
    microsec100 = struct.unpack_from("<H", data, 14)[0]
    us = microsec100 * 100
    time = np.datetime64(
        f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{sec:02d}.{us:06d}"
    )

    pressure = struct.unpack_from("<I", data, 20)[0] * 0.001

    bt_data = data[offset_of_data:]
    beam_dists = []
    for i in range(4):
        d_mm = struct.unpack_from("<i", bt_data, 24 + i * 4)[0]
        if d_mm > 0:
            beam_dists.append(d_mm / 1000.0)

    return Ad2cpBottomTrack(
        time=time,
        pressure=pressure,
        beam_distances_m=beam_dists,
        beam_tilt_deg=beam_tilt_deg,
    )


def read_ad2cp(path: Path | str) -> xr.Dataset:
    """Read a Nortek Signature ``.ad2cp`` file.

    Extracts echosounder data into an xarray Dataset suitable for
    Sv computation.  If the file contains no echosounder packets,
    raises ``ValueError``.

    Parameters
    ----------
    path : Path or str
        Path to the ``.ad2cp`` binary file.

    Returns
    -------
    xr.Dataset
        Dataset with dimensions ``(ping_time, range_sample, frequency)``
        and variables:

        - ``echo_amplitude``: raw uint16 echo counts
        - ``sound_speed``, ``temperature``, ``pressure``: per-ping
        - ``heading``, ``pitch``, ``roll``: per-ping orientation

        Attributes include the parsed ``Ad2cpConfig``.
    """
    import xarray as xr

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"AD2CP file not found: {path}")
    if path.suffix.lower() not in (".ad2cp",):
        raise ValueError(f"Expected .ad2cp file, got: {path.suffix}")

    config: Ad2cpConfig | None = None
    echo_pings: list[Ad2cpEchoPing] = []
    bt_records: list[Ad2cpBottomTrack] = []

    with open(path, "rb") as f:
        file_size = f.seek(0, 2)
        f.seek(0)

        while f.tell() < file_size - 10:
            pos = f.tell()
            hdr = f.read(10)
            if len(hdr) < 10 or hdr[0] != _SYNC_BYTE:
                break

            hdr_size = hdr[1]
            id_byte = hdr[2]

            # For extended IDs (0x23, 0x24) data_size is 4 bytes
            if id_byte in (0x23, 0x24):
                # Re-read header with 12-byte size
                f.seek(pos)
                hdr = f.read(12)
                if len(hdr) < 12:
                    break
                data_size = struct.unpack_from("<I", hdr, 4)[0]
            else:
                data_size = struct.unpack_from("<H", hdr, 4)[0]

            data = f.read(data_size)
            if len(data) < data_size:
                break

            if id_byte == _ID_STRING:
                text = data.decode("ascii", errors="replace")
                config = _parse_config(text)

            elif id_byte == _ID_ECHOSOUNDER:
                echo_pings.append(_parse_echo_data_record(data))

            elif id_byte == _ID_BOTTOM_TRACK:
                bt_records.append(_parse_bt_data_record(data))

    if config is None:
        config = Ad2cpConfig()

    if not echo_pings:
        raise ValueError(
            f"No echosounder data found in {path.name}. "
            "File may contain only velocity/bottom-track data."
        )

    n_freqs = max(len(config.frequencies), 1)
    n_pings_total = len(echo_pings)
    n_pings = n_pings_total // n_freqs

    if n_pings == 0:
        raise ValueError(f"Insufficient echosounder pings in {path.name}")

    n_cells = echo_pings[0].amplitude.shape[0]
    blanking = config.blanking if config.blanking > 0 else 2.0
    cell_size = config.cell_size if config.cell_size > 0 else 0.375

    # Build range coordinate
    range_m = blanking + np.arange(n_cells) * cell_size

    # Separate pings by frequency (they alternate: freq0, freq1, freq0, ...)
    amplitude = np.zeros((n_freqs, n_pings, n_cells), dtype=np.uint16)
    times = np.empty(n_pings, dtype="datetime64[us]")
    sound_speed = np.empty(n_pings, dtype=np.float32)
    temperature = np.empty(n_pings, dtype=np.float32)
    pressure = np.empty(n_pings, dtype=np.float32)
    heading = np.empty(n_pings, dtype=np.float32)
    pitch = np.empty(n_pings, dtype=np.float32)
    roll = np.empty(n_pings, dtype=np.float32)

    for i in range(n_pings):
        base_idx = i * n_freqs
        ping = echo_pings[base_idx]
        times[i] = ping.time
        sound_speed[i] = ping.sound_speed
        temperature[i] = ping.temperature
        pressure[i] = ping.pressure
        heading[i] = ping.heading
        pitch[i] = ping.pitch
        roll[i] = ping.roll
        for fi in range(n_freqs):
            p = echo_pings[base_idx + fi]
            n = min(p.amplitude.shape[0], n_cells)
            amplitude[fi, i, :n] = p.amplitude[:n]

    freq_coord = np.array(config.frequencies[:n_freqs], dtype=np.int32) if config.frequencies else np.array([0], dtype=np.int32)
    cal_offsets = config.cal_offsets[:n_freqs] if config.cal_offsets else [0.0] * n_freqs

    # --- Bottom track → per-ping water depth ---
    # Determine beam tilt from BEAMCFGLIST config (beams 1-4),
    # default 20° for Signature series.
    beam_tilt = 20.0
    if config.raw.get("BEAMCFGLIST"):
        theta = config.raw["BEAMCFGLIST"].get("THETA")
        if theta and isinstance(theta, (int, float)) and theta > 0:
            beam_tilt = float(theta)

    bottom_depth = np.full(n_pings, np.nan, dtype=np.float32)
    if bt_records:
        cos_tilt = np.cos(np.radians(beam_tilt))
        bt_times = np.array([bt.time for bt in bt_records], dtype="datetime64[us]")
        bt_depths = np.array(
            [
                np.mean(bt.beam_distances_m) * cos_tilt + bt.pressure
                if bt.beam_distances_m
                else np.nan
                for bt in bt_records
            ],
            dtype=np.float32,
        )
        # Match BT to echo pings by nearest time
        for pi in range(n_pings):
            diffs = np.abs((bt_times - times[pi]).astype(np.int64))
            nearest = np.argmin(diffs)
            # Only use BT if within 10 seconds of the echo ping
            if diffs[nearest] < 10_000_000:  # 10s in µs
                bottom_depth[pi] = bt_depths[nearest]
        bt_coverage = np.count_nonzero(~np.isnan(bottom_depth))
        logger.info(
            "AD2CP BT: %d records, tilt %.0f°, depth %.1f ± %.1f m, "
            "matched %d/%d pings",
            len(bt_records),
            beam_tilt,
            float(np.nanmean(bt_depths)),
            float(np.nanstd(bt_depths)),
            bt_coverage,
            n_pings,
        )

    ds = xr.Dataset(
        {
            "echo_amplitude": (
                ["frequency", "ping_time", "range_sample"],
                amplitude,
                {
                    "long_name": "Echo amplitude",
                    "units": "count",
                    "comment": "Raw uint16 echo intensity with instrument TVG applied",
                },
            ),
            "sound_speed": (
                ["ping_time"],
                sound_speed,
                {"long_name": "Sound speed", "units": "m s-1"},
            ),
            "temperature": (
                ["ping_time"],
                temperature,
                {"long_name": "Water temperature", "units": "degree_C"},
            ),
            "pressure": (
                ["ping_time"],
                pressure,
                {"long_name": "Pressure", "units": "dbar"},
            ),
            "heading": (
                ["ping_time"],
                heading,
                {"long_name": "Instrument heading", "units": "degree"},
            ),
            "pitch": (
                ["ping_time"],
                pitch,
                {"long_name": "Pitch", "units": "degree"},
            ),
            "roll": (
                ["ping_time"],
                roll,
                {"long_name": "Roll", "units": "degree"},
            ),
            "bottom_depth": (
                ["ping_time"],
                bottom_depth,
                {
                    "long_name": "Bottom depth from bottom track",
                    "units": "m",
                    "comment": (
                        "Vertical water depth derived from bottom-track "
                        "slant range (cos-corrected for beam tilt) plus "
                        "instrument pressure. NaN where no BT data."
                    ),
                },
            ),
        },
        coords={
            "ping_time": times,
            "range_sample": range_m,
            "frequency": freq_coord,
        },
        attrs={
            "instrument_type": config.instrument_type,
            "serial_number": config.serial_number,
            "firmware_version": f"{config.firmware_version}.{config.firmware_minor}",
            "n_cells": n_cells,
            "cell_size_m": cell_size,
            "blanking_m": blanking,
            "cal_offsets_db": cal_offsets,
            "pulse_lengths_ms": config.pulse_lengths[:n_freqs],
            "source_file": path.name,
        },
    )

    logger.info(
        "AD2CP: %s — %d pings × %d cells × %d freq (%s kHz), range %.1f–%.1f m",
        path.name,
        n_pings,
        n_cells,
        n_freqs,
        "/".join(str(f) for f in freq_coord),
        range_m[0],
        range_m[-1],
    )

    return ds


def scan_ad2cp_files(directory: Path | str) -> list[Path]:
    """Find all ``.ad2cp`` files in a directory.

    Parameters
    ----------
    directory : Path or str
        Directory to scan.

    Returns
    -------
    list[Path]
        Sorted list of ``.ad2cp`` file paths.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    return sorted(
        p for p in directory.iterdir() if p.suffix.lower() == ".ad2cp"
    )
