def create_lat_lon_bins(data, lat_bins, lon_bins):
    """
    Partition measurements into latitude and longitude bins.

    Parameters:
    - data: DataFrame with 'latitude' and 'longitude' columns.
    - lat_bins: List of latitude bin edges.
    - lon_bins: List of longitude bin edges.

    Returns:
    - DataFrame with additional 'lat_bin' and 'lon_bin' columns indicating
      the bin each measurement falls into.
    """
    import pandas as pd

    data['lat_bin'] = pd.cut(data['latitude'], bins=lat_bins, labels=False)
    data['lon_bin'] = pd.cut(data['longitude'], bins=lon_bins, labels=False)

    return data


def aggregate_binned_data(data):
    """
    Aggregate the binned oceanographic measurements.

    Parameters:
    - data: A DataFrame containing binned oceanographic measurements.

    Returns:
    - aggregated_data: A DataFrame with aggregated measurements for each bin.
    """
    aggregated_data = data.groupby(['lat_bin', 'lon_bin']).agg('mean').reset_index()
    return aggregated_data


def suggest_lat_lon_bins_from_data(
    data,
    *,
    lat_step: float = 2.0,
    lon_step: float = 2.0,
    lat_margin: float = 1.0,
    lon_margin: float = 1.0,
    clamp_lat: tuple | None = (-15.0, 15.0),
    clamp_lon: tuple | None = None,
):
    """
    Suggest efficient latitude/longitude bin edges based on the data extents.
    """
    import numpy as np
    import pandas as pd

    if not isinstance(data, (pd.DataFrame,)):
        raise TypeError("data must be a pandas DataFrame with 'latitude' and 'longitude' columns")
    if 'latitude' not in data or 'longitude' not in data:
        raise ValueError("data must include 'latitude' and 'longitude' columns")

    lat_min = float(np.nanmin(data['latitude'].to_numpy()))
    lat_max = float(np.nanmax(data['latitude'].to_numpy()))
    lon_min = float(np.nanmin(data['longitude'].to_numpy()))
    lon_max = float(np.nanmax(data['longitude'].to_numpy()))

    lat_min -= lat_margin; lat_max += lat_margin
    lon_min -= lon_margin; lon_max += lon_margin

    def apply_clamp(vmin, vmax, clamp):
        if clamp is None: return vmin, vmax
        a, b = clamp
        new_min = max(vmin, min(a, b)); new_max = min(vmax, max(a, b))
        if new_min >= new_max: return vmin, vmax
        return new_min, new_max

    lat_min, lat_max = apply_clamp(lat_min, lat_max, clamp_lat)
    lon_min, lon_max = apply_clamp(lon_min, lon_max, clamp_lon)

    lat_min = max(-90.0, lat_min); lat_max = min(90.0, lat_max)
    lon_min = max(-180.0, lon_min); lon_max = min(180.0, lon_max)

    def round_down(x, step): return step * np.floor(x / step)
    def round_up(x, step): return step * np.ceil(x / step)

    lat_start = round_down(lat_min, lat_step); lat_end = round_up(lat_max, lat_step)
    lon_start = round_down(lon_min, lon_step); lon_end = round_up(lon_max, lon_step)

    lat_bins = list(np.arange(lat_start, lat_end + 0.5 * lat_step, lat_step))
    lon_bins = list(np.arange(lon_start, lon_end + 0.5 * lon_step, lon_step))

    lat_bins = sorted(set(lat_bins)); lon_bins = sorted(set(lon_bins))
    if len(lat_bins) < 2: lat_bins = [lat_start, lat_end]
    if len(lon_bins) < 2: lon_bins = [lon_start, lon_end]
    return lat_bins, lon_bins
