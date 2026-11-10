"""Multibeam processing: Kongsberg .all files to soundings, bathymetry and backscatter grids.

Reading needs the optional dependency themachinethatgoesping (``multibeam`` extra);
gridding needs rasterio and pyproj.
"""

from .processor import build_mosaic, process, process_files

__all__ = ["build_mosaic", "process", "process_files"]
