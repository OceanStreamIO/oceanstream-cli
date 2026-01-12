"""
Raster generation for oceanographic heatmaps.

Generates Cloud Optimized GeoTIFF (COG) files from interpolated
measurement data for fast web visualization.
"""
from .heatmap import generate_measurement_cog, generate_all_heatmaps

__all__ = ['generate_measurement_cog', 'generate_all_heatmaps']
