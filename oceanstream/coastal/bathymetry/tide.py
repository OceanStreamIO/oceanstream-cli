"""Tide — from chart datum to the water level that was actually there.

EMODnet depths are referenced to Lowest Astronomical Tide. The retrieval needs
depth below the water surface *at the moment the sensor looked*, and those differ
by the tide. At Sesimbra the gap runs to about 1.3 m on average, with per-date
offsets of −1.92, −1.09 and −0.58 m across three survey dates — a spread that
tracks tide state, as LAT-referenced LiDAR against dive-time water level should.

Why this is worth a module rather than a constant
--------------------------------------------------
A uniform offset is *harmless* for the depth-invariance diagnostic: adding a
constant to every depth shifts the regression intercept and leaves the slope,
which is what the diagnostic tests, untouched. It is *not* harmless for anything
absolute. z_max — the depth past which the bottom signal falls below the
atmospheric-correction floor — is a claim about a real depth, and 1.3 m of
uncorrected datum error at a z_max near 12 m is a 10% error in the headline
number. Likewise the depth strata a validation reports against: a 7–8 m stratum
and a 5.5–6.5 m stratum are not the same stratum.

The circularity trap
--------------------
The prototype could have removed its offset by regressing LiDAR depth on diver
depth — the fit is ``hr = 1.013 · diver − 1.48``, a slope of essentially one with
a non-zero intercept, which is exactly the signature of a datum offset. It
deliberately did not, and neither does this module by default: fitting the offset
to the diver depths and then validating against those same diver depths would
make the validation circular, and the resulting MAE would measure nothing but the
residual of a fit to itself. The honest correction comes from an independent tide
prediction. :func:`fit_offset_from_reference` exists because there are legitimate
uses for the fit — quantifying the offset, sanity-checking a model — but it
refuses to hand back a bare number, returning a
:class:`TideCorrection` marked ``derivation="fitted"`` so the circularity travels
with the value.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)

#: Datum corrections larger than this are almost certainly a sign error or a
#: mismatched datum rather than a tide. The largest tidal ranges on Earth
#: (Fundy, Severn) reach ~8 m amplitude; 15 m is well past any of them.
IMPLAUSIBLE_OFFSET_M = 15.0


@dataclass(frozen=True)
class TideCorrection:
    """A water-level offset, and where it came from.

    ``offset_m`` is the height of the water surface above the reference datum,
    in metres, positive up. Instantaneous depth is the datum depth *plus* this:
    a high tide puts more water over the seabed.
    """

    offset_m: float
    #: ``"model"`` — an independent tide prediction. ``"declared"`` — a constant
    #: the operator asserted. ``"fitted"`` — regressed against reference depths,
    #: and therefore unusable for validating against those same depths.
    derivation: str
    source: str
    when: dt.datetime | None = None
    #: Standard error where the derivation supplies one.
    uncertainty_m: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not np.isfinite(self.offset_m):
            raise ValueError(f"TideCorrection.offset_m must be finite; got {self.offset_m}.")
        if not self.source.strip():
            raise ValueError("A tide correction requires its source.")
        if self.uncertainty_m is not None and (
            not np.isfinite(self.uncertainty_m) or self.uncertainty_m < 0
        ):
            raise ValueError("Tide uncertainty must be finite and nonnegative.")
        if abs(self.offset_m) > IMPLAUSIBLE_OFFSET_M:
            raise ValueError(
                f"Tide offset of {self.offset_m:.2f} m exceeds the plausible range "
                f"(±{IMPLAUSIBLE_OFFSET_M:g} m). This is usually a sign error or a "
                "datum mismatch — check that the model reports height above the "
                "same datum the bathymetry is referenced to."
            )
        if self.derivation not in {"model", "declared", "fitted"}:
            raise ValueError(
                f"Unknown derivation {self.derivation!r}; expected 'model', 'declared' or 'fitted'."
            )

    @property
    def is_independent(self) -> bool:
        """Whether this correction may be used ahead of a validation.

        False for a fitted offset: applying it and then validating against the
        depths it was fitted to measures the residual of a fit to itself.
        """
        return self.derivation != "fitted"

    def apply(self, depth_datum_m: np.ndarray) -> np.ndarray:
        """Convert datum-referenced depth to instantaneous depth.

        Depths that go non-positive under the correction — seabed above the
        water surface, i.e. exposed at that tide state — become NaN rather than
        negative numbers that would silently propagate into a log-ratio.
        """
        corrected = np.asarray(depth_datum_m, dtype=np.float32) + np.float32(self.offset_m)
        return np.where(corrected > 0.0, corrected, np.nan).astype(np.float32)

    def to_dict(self) -> dict[str, Any]:
        return {
            "offset_m": round(self.offset_m, 4),
            "derivation": self.derivation,
            "source": self.source,
            "when": None if self.when is None else self.when.isoformat(),
            "uncertainty_m": (None if self.uncertainty_m is None else round(self.uncertainty_m, 4)),
            "is_independent": self.is_independent,
            **({"metadata": self.metadata} if self.metadata else {}),
        }


@runtime_checkable
class TideProvider(Protocol):
    """Anything that can say how high the water was at a place and time."""

    name: str

    def water_level(self, lon: float, lat: float, when: dt.datetime) -> TideCorrection:
        """Height of the water surface above the reference datum, positive up."""
        ...


@dataclass
class ConstantTide:
    """A single offset the operator has asserted for the whole AOI.

    Appropriate when an independent prediction for the acquisition time is
    already in hand — a harbour gauge reading, a published tide table — and the
    AOI is small enough that spatial variation in the tide is negligible.
    """

    offset_m: float
    source: str = "operator-declared"
    name: str = "constant"

    def water_level(self, lon: float, lat: float, when: dt.datetime) -> TideCorrection:
        return TideCorrection(
            offset_m=self.offset_m,
            derivation="declared",
            source=self.source,
            when=when,
        )


@dataclass
class NullTide:
    """No correction. Depths stay on their reference datum.

    The honest default when no tide model is available: it leaves the bias in
    place *and says so*, which a silently-zero offset would not. Every product
    computed through it carries ``derivation="declared"`` with a source naming
    the omission, so the caveat reaches the run report.
    """

    name: str = "none"

    def water_level(self, lon: float, lat: float, when: dt.datetime) -> TideCorrection:
        return TideCorrection(
            offset_m=0.0,
            derivation="declared",
            source=(
                "no tide model configured — depths remain on the bathymetry's "
                "reference datum and absolute depth claims carry that bias"
            ),
            when=when,
        )


@dataclass
class PyTMDTide:
    """Tide from a global model via ``pyTMD`` (FES2022, TPXO, GOT).

    Left unwired deliberately. Each of these models needs its constituent grids
    downloaded under a licence the user has to accept themselves, so there is
    nothing sensible to default to — and a tide provider that silently returned
    the wrong model's answer would be worse than one that refuses.
    """

    model: str = "FES2022"
    model_directory: str | None = None
    name: str = "pytmd"

    def water_level(self, lon: float, lat: float, when: dt.datetime) -> TideCorrection:
        raise NotImplementedError(
            f"The {self.model} provider is not wired up. Tide constituent grids are "
            "distributed under per-model licences and cannot be bundled. Either "
            "supply an independent prediction via ConstantTide(offset_m=...), or "
            "implement the TideProvider protocol against your own pyTMD/FES setup — "
            "it is a single method returning a TideCorrection."
        )


def correct_depth(
    depth_datum_m: np.ndarray,
    correction: TideCorrection,
    require_independent: bool = False,
) -> np.ndarray:
    """Apply a tide correction to a depth grid.

    ``require_independent`` is the guard against the circularity described in the
    module docstring: set it on any path that feeds a validation against the same
    reference depths the offset might have been fitted to.
    """
    if require_independent and not correction.is_independent:
        raise ValueError(
            f"Refusing to apply a {correction.derivation!r} tide offset on a path "
            "that requires an independent one. This offset was regressed against "
            "reference depths; using it and then validating against those depths "
            "would make the validation circular. Supply a model or gauge-derived "
            "offset instead."
        )
    return correction.apply(depth_datum_m)


def fit_offset_from_reference(
    modelled_depth_m: np.ndarray,
    reference_depth_m: np.ndarray,
    source: str = "regression against reference depths",
) -> TideCorrection:
    """Estimate a datum offset by regressing modelled depth on reference depth.

    Returns a correction marked ``derivation="fitted"``, which
    :func:`correct_depth` will refuse to apply on a validation path. Use it to
    *quantify* an offset — a slope near 1 with a non-zero intercept is a datum
    problem, a slope far from 1 is something else and a tide correction will not
    fix it — not to remove one before measuring accuracy against the same points.
    """
    modelled = np.asarray(modelled_depth_m, dtype=float).ravel()
    reference = np.asarray(reference_depth_m, dtype=float).ravel()
    if modelled.shape != reference.shape:
        raise ValueError(
            f"modelled_depth_m {modelled.shape} and reference_depth_m "
            f"{reference.shape} must have the same number of points."
        )
    valid = np.isfinite(modelled) & np.isfinite(reference)
    n = int(valid.sum())
    if n < 3:
        raise ValueError(f"Need at least 3 paired finite depths to fit an offset; got {n}.")
    slope, intercept = np.polyfit(reference[valid], modelled[valid], 1)
    residual = modelled[valid] - (slope * reference[valid] + intercept)
    return TideCorrection(
        offset_m=float(-intercept),
        derivation="fitted",
        source=source,
        uncertainty_m=float(np.std(residual, ddof=2)) if n > 2 else None,
        metadata={
            "slope": round(float(slope), 4),
            "intercept_m": round(float(intercept), 4),
            "n_points": n,
            "interpretation": (
                "A slope near 1 with a non-zero intercept is a datum offset. A "
                "slope far from 1 is a scale or sign problem that a tide "
                "correction will not fix."
            ),
            "circularity_warning": (
                "Fitted to the reference depths. Do not apply this and then "
                "validate against the same depths."
            ),
        },
    )


def get_provider(name: str | None, **kwargs: Any) -> TideProvider:
    """Look up a tide provider by name. ``None`` yields :class:`NullTide`."""
    if name is None or name.lower() in {"none", "null"}:
        return NullTide()
    key = name.strip().lower()
    if key == "constant":
        return ConstantTide(**kwargs)
    if key in {"pytmd", "fes2022", "fes", "tpxo", "got"}:
        return PyTMDTide(model=("FES2022" if key in {"pytmd", "fes"} else name.upper()), **kwargs)
    raise KeyError(
        f"Unknown tide model {name!r}. Known: 'none', 'constant', 'pytmd' "
        "(FES2022/TPXO/GOT). Or pass any object implementing the TideProvider "
        "protocol directly."
    )
