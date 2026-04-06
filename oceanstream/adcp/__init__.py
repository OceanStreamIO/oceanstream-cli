"""ADCP processing submodule for oceanstream.

This submodule handles processing of Acoustic Doppler Current Profiler data.
Reads RDI (Teledyne) binary files via dolfyn, applies beam-to-earth
coordinate transforms, and produces time-averaged velocity profiles.
"""
from .processor import process, process_file

__all__ = ["process", "process_file"]
