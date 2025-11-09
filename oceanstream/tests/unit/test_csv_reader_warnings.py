import warnings
from pathlib import Path
import pandas as pd

from oceanstream.geotrack.csv_reader import read_csv_files


def _write_csv(path: Path, header: list[str], rows: list[list[str]]):
    path.write_text(
        "\n".join(
            [",".join(header)] + [",".join(map(str, r)) for r in rows]
        ),
        encoding="utf-8",
    )


def test_read_csv_files_no_futurewarning_on_concat(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()

    # File 1: has a fully NA column 'empty_col' and valid lat/lon
    f1 = raw / "sd1234_sample1.csv"
    _write_csv(
        f1,
        ["latitude", "longitude", "empty_col"],
        [
            ["34.1", "-120.5", ""],
            ["34.2", "-120.6", ""],
        ],
    )

    # File 2: has only latitude/longitude
    f2 = raw / "sd1234_sample2.csv"
    _write_csv(
        f2,
        ["latitude", "longitude"],
        [
            ["34.3", "-120.7"],
        ],
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        df = read_csv_files(str(raw))
        # Ensure no FutureWarning was raised
        assert not any(issubclass(warn.category, FutureWarning) for warn in w)

    # Basic sanity checks
    assert not df.empty
    assert set(["latitude", "longitude", "platform_id"]).issubset(df.columns)
    # The all-NA column should be dropped
    assert "empty_col" not in df.columns
