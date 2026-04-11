"""ADCP processing submodule for oceanstream.

This submodule handles processing of Acoustic Doppler Current Profiler data.

- **RDI (Teledyne)**: Reads ``.raw`` binary files via dolfyn, applies
  beam-to-earth coordinate transforms, and produces time-averaged velocity
  profiles.
- **Nortek AD2CP**: Reads ``.ad2cp`` binary files directly (no dolfyn),
  extracts echosounder amplitude, and computes Sv.
"""
from .processor import process, process_ad2cp_file, process_file

__all__ = ["process", "process_file", "process_ad2cp_file"]
