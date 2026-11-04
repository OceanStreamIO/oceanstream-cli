"""A recorder clock that was set wrong is corrected everywhere at once."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import xarray as xr

from oceanstream.echodata.time_correction import (
    shift_dataset_times,
    shift_datetime,
    shift_echodata_times,
)

SEVEN_HOURS = -7 * 3600


def test_file_time_moves_into_the_previous_utc_day() -> None:
    assert shift_datetime(datetime(2019, 7, 10, 3, 0), SEVEN_HOURS) == datetime(2019, 7, 9, 20, 0)
    assert shift_datetime(None, SEVEN_HOURS) is None
    assert shift_datetime(datetime(2019, 7, 10), 0) == datetime(2019, 7, 10)


def _beam() -> xr.Dataset:
    t = np.array(["2019-07-10T03:00:00", "2019-07-10T03:00:02"], dtype="datetime64[ns]")
    return xr.Dataset({"backscatter_r": (("ping_time",), [1.0, 2.0]),
                       "time_stamp": (("ping_time",), t)},
                      coords={"ping_time": t, "channel": "38"})


def test_every_datetime_is_shifted_and_values_kept() -> None:
    out = shift_dataset_times(_beam(), SEVEN_HOURS)
    assert str(out.ping_time.values[0]) == "2019-07-09T20:00:00.000000000"
    assert str(out.time_stamp.values[1]) == "2019-07-09T20:00:02.000000000"
    assert out.backscatter_r.values.tolist() == [1.0, 2.0]
    assert out.attrs["oceanstream_time_offset_s"] == SEVEN_HOURS
    assert shift_dataset_times(_beam(), 0).identical(_beam())


class FakeEchoData:
    def __init__(self) -> None:
        self.groups = {"Top-level": xr.Dataset(), "Sonar/Beam_group1": _beam(),
                       "Platform": xr.Dataset(coords={"time1": _beam().ping_time.values})}

    @property
    def group_paths(self) -> list[str]:
        return list(self.groups)

    def __getitem__(self, key: str) -> xr.Dataset:
        return self.groups[key]

    def __setitem__(self, key: str, value: xr.Dataset) -> None:
        self.groups[key] = value


def test_all_groups_of_an_echodata_are_shifted() -> None:
    ed = shift_echodata_times(FakeEchoData(), SEVEN_HOURS)
    assert str(ed["Platform"].time1.values[0]) == "2019-07-09T20:00:00.000000000"
    assert str(ed["Sonar/Beam_group1"].ping_time.values[0]).startswith("2019-07-09T20")
