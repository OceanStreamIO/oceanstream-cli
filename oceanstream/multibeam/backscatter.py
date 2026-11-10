"""Angular response of multibeam backscatter, and its removal for mosaics."""

from __future__ import annotations

from typing import Any

import numpy as np


class AngularResponse:
    """Median backscatter per beam angle, kept as histograms so batches can be added.

    Seabed backscatter depends on the beam angle as well as on the seabed, which shows
    in a mosaic as stripes along the track. :meth:`flatten` removes the median response
    of the survey (or day) and puts every sounding on the level of the reference angles,
    30-50 degrees by default, where the response is flattest.
    """

    def __init__(  # noqa: PLR0913 - histogram geometry
        self,
        *,
        angle_step_deg: float = 1.0,
        max_angle_deg: float = 90.0,
        bs_min_db: float = -100.0,
        bs_max_db: float = 20.0,
        bs_step_db: float = 0.1,
        min_count: int = 100,
    ) -> None:
        self.angle_step = angle_step_deg
        self.n_angles = int(round(max_angle_deg / angle_step_deg))
        self.bs_min = bs_min_db
        self.bs_step = bs_step_db
        self.n_bs = int(round((bs_max_db - bs_min_db) / bs_step_db))
        self.min_count = min_count
        self.counts = np.zeros((self.n_angles, self.n_bs), dtype=np.int64)

    def _angle_bin(self, angle_deg: np.ndarray) -> np.ndarray:
        a = np.floor(np.abs(np.asarray(angle_deg, dtype=np.float64)) / self.angle_step)
        out: np.ndarray = np.clip(np.nan_to_num(a, nan=-1), -1, self.n_angles - 1)
        return out.astype(np.int64)

    def add(self, angle_deg: np.ndarray, backscatter_db: np.ndarray) -> None:
        """Add soundings to the histograms; NaN values are skipped."""
        ai = self._angle_bin(angle_deg)
        bs = np.asarray(backscatter_db, dtype=np.float64)
        bi = np.floor((bs - self.bs_min) / self.bs_step)
        ok = (ai >= 0) & np.isfinite(bi)
        bi = np.clip(bi[ok], 0, self.n_bs - 1).astype(np.int64)
        flat = ai[ok] * self.n_bs + bi
        self.counts += np.bincount(flat, minlength=self.counts.size).reshape(self.counts.shape)

    def _median(self, hist: np.ndarray) -> float:
        total = int(hist.sum())
        if total < self.min_count:
            return float("nan")
        k = int(np.searchsorted(np.cumsum(hist), total / 2.0))
        return self.bs_min + (k + 0.5) * self.bs_step

    def medians(self) -> np.ndarray:
        """Median backscatter (dB) per angle bin; NaN where a bin has too few soundings."""
        return np.array([self._median(h) for h in self.counts])

    def reference_db(self, low_deg: float = 30.0, high_deg: float = 50.0) -> float:
        """Median backscatter over the reference angles."""
        lo = int(low_deg / self.angle_step)
        hi = int(high_deg / self.angle_step)
        return self._median(self.counts[lo:hi].sum(axis=0))

    def flatten(self, angle_deg: np.ndarray, backscatter_db: np.ndarray) -> np.ndarray:
        """Backscatter with the angular response removed (NaN where it is unknown)."""
        med = self.medians()
        ai = self._angle_bin(angle_deg)
        level = np.where(ai >= 0, med[np.clip(ai, 0, None)], np.nan)
        out: np.ndarray = np.asarray(backscatter_db, dtype=np.float64) - level
        return (out + self.reference_db()).astype(np.float32)

    def to_dict(self) -> dict[str, Any]:
        """Angle bins, their medians and counts, and the reference level, for summaries."""
        med = self.medians()
        n = self.counts.sum(axis=1)
        keep = n > 0
        return {
            "angle_deg": [
                round(float(a), 2) for a in (np.arange(self.n_angles) * self.angle_step)[keep]
            ],
            "median_db": [None if np.isnan(m) else round(float(m), 2) for m in med[keep]],
            "soundings": [int(c) for c in n[keep]],
            "reference_db": _round(self.reference_db()),
        }


def _round(x: float) -> float | None:
    return None if np.isnan(x) else round(float(x), 2)
