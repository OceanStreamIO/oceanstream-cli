"""Echodata processing submodule for oceanstream.

This submodule handles processing of echosounder data (EK60/EK80) into Zarr format using echopype.
"""
from .processor import process

__all__ = ["process"]
