"""Reference AOIs — the sites this library's behaviour is actually measured at.

These are shipped as package data for the same reason ``sensors/definitions``
are: they describe the world, not a checkout, so they should not have to be
reconstructed by hand at every call site. Phase 4 compares retrieval across
sites, and a comparison table is only meaningful if every run started from the
same geometry and the same depth grid.

Each file is exactly :meth:`AOI.to_dict` output, so it round-trips through
:meth:`AOI.from_json` with nothing bespoke in between. Adding a site is adding a
file.

Sesimbra's geometry comes from the ``lee_demo`` prototype, not from the
KelpObserve project documentation. Those are different boxes: the project AOI
is the Arrábida field-campaign strip, while the reference retrievals were
produced at the Cabo Espichel end, where the diver quadrats are. The golden
values Phase 4.2 is measured against belong to the latter, so shipping the
former would compare against numbers from somewhere else.

What is *not* recorded is as deliberate as what is. ``habitat_bbox`` marks where
benthic claims may be made and is declared only at Sesimbra, which has a diver
survey; at Donegal and the Summer Isles there is none yet, and drawing a box
would assert one. ``survey_year`` and ``seabed_stability`` are likewise absent
there, because EMODnet publishes neither — the WFS returns a product release,
and a release year is not a survey year. Sesimbra has both because the source
programme is known independently: 2011 DGT/APA airborne LiDAR over rocky reef.
``seabed_stability`` decides whether depth MAE may carry the accuracy claim, so
it is recorded from the survey or not at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oceanstream.coastal.aoi import AOI

_DIR = Path(__file__).parent


def list_reference_aois() -> list[str]:
    """Names of the shipped AOIs, sorted."""
    return sorted(path.stem for path in _DIR.glob("*.json"))


def get_reference_aoi(name: str) -> AOI:
    """Load a shipped AOI by name.

    :raises KeyError: if no AOI of that name ships with the library.
    """
    path = _DIR / f"{name}.json"
    if not path.is_file():
        available = ", ".join(list_reference_aois()) or "none"
        raise KeyError(f"No reference AOI named {name!r}. Available: {available}.")
    return AOI.from_json(path)


def load_reference_aois() -> dict[str, AOI]:
    """Every shipped AOI, keyed by name."""
    return {name: get_reference_aoi(name) for name in list_reference_aois()}


def _raw(name: str) -> dict[str, Any]:
    """The on-disk payload, unparsed. For tests that check the file itself."""
    payload: dict[str, Any] = json.loads((_DIR / f"{name}.json").read_text())
    return payload


__all__ = ["get_reference_aoi", "list_reference_aois", "load_reference_aois"]
