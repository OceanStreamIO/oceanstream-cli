"""Sensor profiles — the *instrument*, and nothing else.

A profile answers three questions the physics cannot answer for itself:

1. Which observed band plays which role (blue, green, red, NIR, SWIR)?
2. Which band is the sun-glint reference?
3. Which band is the QC null channel — the one where pure-water absorption
   guarantees no bottom return, so whatever structure is left there is
   atmospheric correction plus sensor noise?

Everything downstream of that is a function of wavelength and IOPs, so the core
physics (``optics.water``, ``inversion.lee``) is already sensor-agnostic and
gets no sensor argument.

Band matching is by proximity, not equality
-------------------------------------------
ACOLITE labels its output files with the platform's RSR-weighted centre
wavelength, and those drift between platforms: the same nominal Sentinel-2 SWIR
band is emitted as 2191 nm by one platform and 2202 nm by another. Matching on
equality would therefore fail on a platform swap, and matching on nearest
without a bound would silently substitute a band that measures something else.
So :meth:`SensorProfile.band_index` matches within an explicit tolerance and
returns ``None`` past it.

Pléiades Neo and the missing SWIR
---------------------------------
Everything that breaks on PNeo traces back to it having no SWIR band:

* **Deglint.** Hedley's correction needs a reference band with zero
  water-leaving reflectance. Sentinel-2 uses SWIR ~1612 nm, where that holds.
  PNeo has to fall back to NIR ~825 nm, which is *not* zero over bright shallow
  sand, so the correction over-subtracts exactly where the bottom signal is
  strongest. Flagged by :attr:`SensorProfile.deglint_reference_is_assumed_dark`.
* **Deep-water screen.** The SWIR screen that dropped 62,503 candidate pixels to
  5,369 at Sesimbra has no PNeo equivalent; the screen has to run on NIR or on
  depth instead.
* **Atmospheric correction.** ACOLITE's dark-spectrum fitting is less
  constrained without SWIR, so the AC residual — and hence ε in the
  detectability calculation — is larger.

Two things get *better*, not worse: the red-edge band near 725 nm is a stronger
null channel than 667 nm (higher pure-water absorption), and 1.2 m pixels are
far more likely to be substrate-homogeneous than 10 m ones. The trade is that
1.2 m imagery against an 11 m depth grid inverts the resolution ratio, so depth
assignment becomes the weak link and PNeo must be aggregated to the bathymetry
grid before any k regression.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

#: Canonical band roles. Not every sensor carries every role.
BandRole = str

#: The wavelengths a scene actually carries, as a list or a numpy array.
Wavelengths = Sequence[float] | np.ndarray

#: Roles the retrieval cannot run without.
REQUIRED_ROLES: tuple[BandRole, ...] = ("blue", "green", "red", "nir")


@dataclass(frozen=True)
class SensorProfile:
    """Instrument-level properties of a multispectral sensor."""

    name: str
    #: ACOLITE's sensor key, used when generating a settings file. ACOLITE
    #: derives this from the input bundle itself, so it is recorded for
    #: provenance rather than passed as an override.
    acolite_sensor: str
    #: Nominal centre wavelengths (nm) the sensor emits, ascending. Observed
    #: labels drift a few nm per platform — see ``band_match_tolerance_nm``.
    bands_nm: tuple[float, ...]
    #: Role → nominal centre wavelength (nm).
    band_roles: dict[BandRole, float]
    #: Multispectral ground sampling distance in metres. PNeo's 0.3 m is
    #: panchromatic; the multispectral bands the retrieval uses are 1.2 m.
    native_gsd_m: float
    #: Band used as the sun-glint reference in a Hedley-style correction.
    deglint_reference_nm: float
    #: Whether that reference band can honestly be assumed dark over water.
    #: False for any NIR fallback — see the module docstring.
    deglint_reference_is_assumed_dark: bool
    #: Bands driving the optically-deep-water screen. Empty when the sensor
    #: has no SWIR, in which case the screen must fall back to NIR or depth.
    deepwater_screen_bands: tuple[float, ...]
    #: QC null channel: pure-water absorption is high enough that no bottom
    #: return survives, so residual spatial structure there is AC plus noise.
    null_channel_nm: float
    #: Half-width for proximity band matching. 15 nm covers the observed
    #: Sentinel-2 platform spread (2191 vs 2202 nm) without reaching an
    #: adjacent band; the narrowest Sentinel-2 gap is 707 → 741 nm.
    band_match_tolerance_nm: float = 15.0
    notes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if tuple(sorted(self.bands_nm)) != tuple(self.bands_nm):
            raise ValueError(f"{self.name}: bands_nm must be ascending; got {self.bands_nm}.")
        missing = [r for r in REQUIRED_ROLES if r not in self.band_roles]
        if missing:
            raise ValueError(
                f"{self.name}: missing required band roles {missing}. "
                f"Declared roles: {sorted(self.band_roles)}."
            )
        for role, nm in self.band_roles.items():
            if nm not in self.bands_nm:
                raise ValueError(
                    f"{self.name}: role {role!r} points at {nm} nm, which is not "
                    f"in bands_nm {self.bands_nm}."
                )

    # -- Capability flags ---------------------------------------------------

    @property
    def has_swir(self) -> bool:
        """Whether the sensor carries a SWIR band at all."""
        return any(nm >= 1000.0 for nm in self.bands_nm)

    @property
    def has_red_edge(self) -> bool:
        return "red_edge" in self.band_roles

    # -- Band lookup --------------------------------------------------------

    def band_index(
        self,
        observed_nm: Wavelengths,
        target_nm: float,
        tolerance_nm: float | None = None,
    ) -> int | None:
        """Index of the observed band nearest ``target_nm``, or ``None``.

        ``None`` rather than the nearest band when nothing lands inside the
        tolerance, so a missing band fails loudly instead of quietly
        substituting a band that measures something else.
        """
        if len(observed_nm) == 0:
            return None
        wavelengths = np.asarray(observed_nm, dtype=float)
        idx = int(np.argmin(np.abs(wavelengths - target_nm)))
        limit = self.band_match_tolerance_nm if tolerance_nm is None else tolerance_nm
        return idx if abs(float(wavelengths[idx]) - target_nm) <= limit else None

    def role_index(
        self,
        observed_nm: Wavelengths,
        role: BandRole,
        tolerance_nm: float | None = None,
    ) -> int | None:
        """Index of the observed band playing ``role``, or ``None`` if absent."""
        nominal = self.band_roles.get(role)
        if nominal is None:
            return None
        return self.band_index(observed_nm, nominal, tolerance_nm)

    def require_role_index(
        self,
        observed_nm: Wavelengths,
        role: BandRole,
        tolerance_nm: float | None = None,
    ) -> int:
        """:meth:`role_index`, but raise with a usable message instead of ``None``."""
        idx = self.role_index(observed_nm, role, tolerance_nm)
        if idx is not None:
            return idx
        nominal = self.band_roles.get(role)
        if nominal is None:
            raise KeyError(
                f"{self.name} declares no {role!r} band. Declared roles: "
                f"{sorted(self.band_roles)}."
            )
        raise KeyError(
            f"{self.name}: no observed band within "
            f"{tolerance_nm or self.band_match_tolerance_nm:g} nm of the {role!r} "
            f"band at {nominal:g} nm. Observed: "
            f"{[round(float(w), 1) for w in observed_nm]}."
        )

    def resolve_roles(
        self, observed_nm: Wavelengths, tolerance_nm: float | None = None
    ) -> dict[BandRole, int]:
        """Map every declared role that the scene actually carries to its index."""
        resolved = {}
        for role in self.band_roles:
            idx = self.role_index(observed_nm, role, tolerance_nm)
            if idx is not None:
                resolved[role] = idx
        return resolved

    def validate_scene_bands(
        self, observed_nm: Wavelengths, tolerance_nm: float | None = None
    ) -> list[str]:
        """Problems with a scene's band set, worst first. Empty means usable.

        Returned rather than raised: a scene missing its SWIR screen is still
        processable, just with a caveat that belongs in the run report.
        """
        problems: list[str] = []
        resolved = self.resolve_roles(observed_nm, tolerance_nm)
        for role in REQUIRED_ROLES:
            if role not in resolved:
                problems.append(
                    f"missing required {role!r} band near "
                    f"{self.band_roles[role]:g} nm"
                )
        if self.band_index(observed_nm, self.deglint_reference_nm, tolerance_nm) is None:
            problems.append(
                f"missing deglint reference near {self.deglint_reference_nm:g} nm; "
                "sun-glint correction cannot run"
            )
        if self.band_index(observed_nm, self.null_channel_nm, tolerance_nm) is None:
            problems.append(
                f"missing null channel near {self.null_channel_nm:g} nm; "
                "the AC-uncertainty and pure-water-floor QC gates cannot run"
            )
        absent_screen = [
            nm
            for nm in self.deepwater_screen_bands
            if self.band_index(observed_nm, nm, tolerance_nm) is None
        ]
        if absent_screen:
            problems.append(
                f"deep-water screen bands {absent_screen} nm absent; "
                "screen must fall back to NIR or depth"
            )
        return problems


# ---------------------------------------------------------------------------
# Shipped profiles
# ---------------------------------------------------------------------------

#: Sentinel-2 MSI. Wavelengths are the ACOLITE L2R ``rhos_*`` labels observed on
#: the Sesimbra scenes the golden regression is built from; other platforms in
#: the constellation shift these by a few nm, which proximity matching absorbs.
SENTINEL2 = SensorProfile(
    name="sentinel2",
    acolite_sensor="S2A_MSI",
    bands_nm=(444.0, 489.0, 561.0, 667.0, 707.0, 741.0, 785.0, 835.0, 866.0, 1612.0, 2191.0),
    band_roles={
        "blue": 444.0,
        "blue_green": 489.0,
        "green": 561.0,
        "red": 667.0,
        "red_edge": 707.0,
        "nir": 835.0,
        "nir_narrow": 866.0,
        "swir1": 1612.0,
        "swir2": 2191.0,
    },
    native_gsd_m=10.0,
    deglint_reference_nm=1612.0,
    deglint_reference_is_assumed_dark=True,
    deepwater_screen_bands=(1612.0, 2191.0),
    null_channel_nm=667.0,
    notes={
        "gsd": (
            "10 m for blue/green/red/NIR; red-edge and SWIR are natively 20 m and "
            "are resampled by ACOLITE."
        ),
    },
)

#: Pléiades Neo, multispectral. Six bands, no SWIR — see the module docstring
#: for what that costs and what it buys.
PLEIADES_NEO = SensorProfile(
    name="pleiades_neo",
    acolite_sensor="PNEO",
    bands_nm=(420.0, 490.0, 560.0, 655.0, 725.0, 825.0),
    band_roles={
        "deep_blue": 420.0,
        "blue": 490.0,
        "green": 560.0,
        "red": 655.0,
        "red_edge": 725.0,
        "nir": 825.0,
    },
    native_gsd_m=1.2,
    deglint_reference_nm=825.0,
    deglint_reference_is_assumed_dark=False,
    deepwater_screen_bands=(),
    null_channel_nm=725.0,
    notes={
        "deglint": (
            "NIR fallback. Hedley assumes the reference band carries no "
            "water-leaving signal; NIR is not dark over bright shallow sand, so "
            "the correction over-subtracts where the bottom signal is strongest."
        ),
        "null_channel": (
            "Red edge at 725 nm rather than red. Pure-water absorption is roughly "
            "twice that at 667 nm, making it a stronger null than Sentinel-2 has."
        ),
        "resolution": (
            "1.2 m multispectral against ~11 m bathymetry inverts the Sentinel-2 "
            "resolution ratio: substrate homogeneity improves, depth assignment "
            "degrades. Aggregate to the bathymetry grid before fitting k."
        ),
        "qaa": (
            "QAA v6 coefficients are tuned on 443/490/555/670 nm. Applying them to "
            "these band centres is an approximation and is flagged as such."
        ),
    },
)


SENSORS: dict[str, SensorProfile] = {
    SENTINEL2.name: SENTINEL2,
    PLEIADES_NEO.name: PLEIADES_NEO,
}

#: Short aliases accepted on the CLI and in scene metadata.
_ALIASES = {
    "s2": "sentinel2",
    "sentinel-2": "sentinel2",
    "msi": "sentinel2",
    "pneo": "pleiades_neo",
    "pleiades-neo": "pleiades_neo",
}


def get_sensor(name: str) -> SensorProfile:
    """Look up a shipped profile by name or alias."""
    key = name.strip().lower().replace(" ", "_")
    key = _ALIASES.get(key, key)
    try:
        return SENSORS[key]
    except KeyError:
        raise KeyError(
            f"Unknown sensor {name!r}. Known: {', '.join(sorted(SENSORS))} "
            f"(aliases: {', '.join(sorted(_ALIASES))}). Construct a SensorProfile "
            "directly to use an unregistered instrument."
        ) from None
