"""A single CTD .hex input converts to a CSV that exists."""

from pathlib import Path

import pandas as pd
import pytest

from oceanstream.geotrack import processor


@pytest.mark.skipif(not processor.HAS_CTD_SUPPORT, reason="seabirdscientific not installed")
@pytest.mark.parametrize("stem", ["11901", "RR2205_011"])
def test_converted_csv_path_is_the_file_written(tmp_path: Path, monkeypatch, stem: str) -> None:
    hex_path = tmp_path / "in" / f"{stem}.hex"
    hex_path.parent.mkdir()
    hex_path.write_text("")

    def fake_process(cast, output_dir):
        # Same naming as sbe911.process_ctd_cast
        df = pd.DataFrame({"prDM": [1.0, 2.0]})
        df.to_csv(output_dir / f"{cast.cruise_id}_{cast.cast_id}.csv", index=False)
        return df

    monkeypatch.setattr(processor, "find_ctd_casts", lambda directory: [])
    monkeypatch.setattr(processor, "process_ctd_cast", fake_process)

    csv_path = processor._convert_ctd_to_csv(hex_path, tmp_path / "work")

    assert csv_path is not None
    assert csv_path.exists()
