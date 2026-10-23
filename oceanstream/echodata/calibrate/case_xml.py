"""Per-channel calibration from a ``<calibration><case .../></calibration>`` XML file.

This is the calibration file the Marine and Freshwater Research Institute (Iceland)
publishes with its EK80 survey data on PANGAEA::

    <calibration>
      <case channel="2" kHz="38" equivalentBeamAngle="-20.30" g="27.03" SA="1024:-0.04"/>
      ...
    </calibration>

``g`` is the transducer gain (dB), ``SA`` the Sa correction (dB) keyed by pulse duration
in microseconds (several as ``512:a;1024:b``), ``equivalentBeamAngle`` in dB re 1 sr. The
values become echopype ``cal_params`` (``gain_correction``, ``sa_correction``,
``equivalent_beam_angle``) for the channels whose nominal frequency matches ``kHz``;
values in the raw files are used for anything the file does not give.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import xarray as xr


def load_case_xml(path: str | Path) -> dict[float, dict]:
    """``{frequency_hz: {"gain": g, "eba": psi, "sa": {pulse_us: sa}}}`` from *path*."""
    root = ET.parse(path).getroot()
    table: dict[float, dict] = {}
    for case in root.iter("case"):
        khz = case.get("kHz")
        if khz is None:
            raise ValueError(f"{path}: <case> without kHz: {ET.tostring(case, encoding='unicode')}")
        entry: dict = {"sa": {}}
        if case.get("g") is not None:
            entry["gain"] = float(case.get("g"))
        if case.get("equivalentBeamAngle") is not None:
            entry["eba"] = float(case.get("equivalentBeamAngle"))
        for item in filter(None, (case.get("SA") or "").split(";")):
            pulse, value = item.split(":")
            entry["sa"][int(pulse)] = float(value)
        table[float(khz) * 1000] = entry
    if not table:
        raise ValueError(f"{path}: no <case> elements; expected <calibration><case kHz=... /></calibration>")
    return table


def case_cal_params(beam: xr.Dataset, table: dict[float, dict]) -> dict[str, xr.DataArray]:
    """echopype ``cal_params`` for the channels of *beam* (a ``Sonar/Beam_groupX``).

    A parameter is only returned when the file gives it for every channel, since
    echopype takes one array per parameter. Sa is chosen by the pulse duration of
    each channel's first recorded ping.
    """
    import numpy as np
    import xarray as xr

    channels = beam["channel"].values
    freqs = np.ravel(beam["frequency_nominal"].values)
    durations = np.atleast_2d(beam["transmit_duration_nominal"].values)
    pulses = [row[np.isfinite(row)][0] if np.isfinite(row).any() else np.nan for row in durations]

    def entry(freq: float) -> dict | None:
        return next((v for f, v in table.items() if np.isclose(f, freq)), None)

    values: dict[str, list] = {"gain_correction": [], "equivalent_beam_angle": [], "sa_correction": []}
    for freq, pulse in zip(freqs, pulses, strict=True):
        e = entry(float(freq)) or {}
        values["gain_correction"].append(e.get("gain"))
        values["equivalent_beam_angle"].append(e.get("eba"))
        pulse_us = int(round(float(pulse) * 1e6)) if np.isfinite(pulse) else None
        values["sa_correction"].append(e.get("sa", {}).get(pulse_us))
    return {
        name: xr.DataArray(np.asarray(vals, dtype=float), dims=["channel"], coords={"channel": channels})
        for name, vals in values.items()
        if all(v is not None for v in vals)
    }
