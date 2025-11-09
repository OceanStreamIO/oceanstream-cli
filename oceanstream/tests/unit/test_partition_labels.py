import re
from pathlib import Path

import pandas as pd
from oceanstream.geotrack.geoparquet_writer import write_geoparquet


def test_partition_label_format_and_structure(tmp_path: Path):
    df = pd.DataFrame(
        {
            "latitude": [-10.0, 10.0],
            "longitude": [-20.0, 20.0],
        }
    )

    lat_bins = [-90, 0, 90]
    lon_bins = [-180, 0, 180]

    out_dir = tmp_path / "dataset"
    write_geoparquet(df, out_dir, lat_bins, lon_bins)

    # Expect top-level directories partitioned by lat_bin=<label>
    lat_dirs = sorted([p for p in out_dir.iterdir() if p.is_dir()])
    assert lat_dirs, "Expected lat_bin partition directories to be created"
    for p in lat_dirs:
        assert p.name.startswith("lat_bin="), f"Unexpected top-level partition: {p.name}"
        label = p.name.split("=", 1)[1]
        # New label format: lat_<low>_<high> with optional decimal part
        assert re.match(r"^lat_-?\d+(?:\.\d+)?_-?\d+(?:\.\d+)?$", label), (
            f"lat_bin label not in expected 'lat_<low>_<high>' format: {label}"
        )

        # Now check lon_bin subdirectories
        lon_dirs = sorted([q for q in p.iterdir() if q.is_dir()])
        assert lon_dirs, f"Expected lon_bin directories under {p.name}"
        for q in lon_dirs:
            assert q.name.startswith("lon_bin="), f"Unexpected second-level partition: {q.name}"
            lon_label = q.name.split("=", 1)[1]
            assert re.match(r"^lon_-?\d+(?:\.\d+)?_-?\d+(?:\.\d+)?$", lon_label), (
                f"lon_bin label not in expected 'lon_<low>_<high>' format: {lon_label}"
            )
