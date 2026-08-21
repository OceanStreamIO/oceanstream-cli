"""Colormap presets used across echogram rendering.

Provides three canonical colormaps for volume backscatter (Sv) echograms:

- ``ocean_r``  — matplotlib built-in; white water column, blue-green scatterers.
                 Good default for tropical / oligotrophic data.
- ``jet``      — matplotlib built-in; high contrast, historically common in
                 fisheries acoustics reports (though not colorblind-safe).
- ``EK500``    — the classic Simrad EK500 discretised palette, ported from
                 the historical batch scripts (build_combined_38khz.py,
                 run_combine_daily.py). Directly comparable to output from
                 legacy EK500 processing.

Use ``get_colormap(name)`` to resolve a name to a matplotlib colormap
object or the built-in name string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    import matplotlib.colors as mcolors


# Simrad EK500 discretised colour palette (12 bins) — matches build_combined_38khz.py
_EK500_COLORS = [
    (1.000, 1.000, 1.000),
    (0.624, 0.624, 0.624),
    (0.373, 0.373, 0.686),
    (0.000, 0.000, 0.498),
    (0.000, 0.000, 0.749),
    (0.000, 0.498, 0.000),
    (0.000, 0.749, 0.000),
    (0.498, 0.749, 0.000),
    (0.749, 0.749, 0.000),
    (0.749, 0.498, 0.000),
    (0.749, 0.000, 0.000),
    (0.498, 0.000, 0.000),
]


def _build_ek500_cmap():
    """Build the EK500 colormap on demand (avoids matplotlib import at module load)."""
    import matplotlib.colors as mcolors

    return mcolors.LinearSegmentedColormap.from_list("EK500", _EK500_COLORS, N=256)


# Canonical colormap names accepted by get_colormap().
CANONICAL_COLORMAP_NAMES = ("ocean_r", "jet", "EK500")


def get_colormap(name: str) -> Union[str, "mcolors.Colormap"]:
    """Return a matplotlib-compatible cmap value for ``name``.

    Built-in matplotlib colormaps ("ocean_r", "jet", "viridis", ...) are
    returned as string names — matplotlib resolves them via ``get_cmap``.
    "EK500" (case-insensitive) returns a discretised ``LinearSegmentedColormap``.

    Unknown names fall back to the string itself (matplotlib will raise
    if it is not a valid colormap).
    """
    lname = name.lower()
    if lname == "ek500":
        return _build_ek500_cmap()
    return name


def resolve_colormap_list(names: list[str]) -> list[tuple[str, Union[str, "mcolors.Colormap"]]]:
    """Resolve a list of colormap names to ``(name, cmap_value)`` pairs.

    The name in each pair is the *sanitised* form suitable for filenames
    (lowercased, spaces replaced with underscores).
    """
    out: list[tuple[str, Union[str, "mcolors.Colormap"]]] = []
    seen: set[str] = set()
    for n in names:
        safe = n.strip().lower().replace(" ", "_")
        if not safe or safe in seen:
            continue
        seen.add(safe)
        out.append((safe, get_colormap(n.strip())))
    return out
