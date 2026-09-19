"""Optional scene-acquisition helpers (CDSE, Planetary Computer).

Only imported when the ``coastal-acquire`` extra is installed. In production
scene discovery lives in EarthStudio (``pineviewlabs/sar-watch``); this
subpackage exists for standalone/CLI/test use only.

The two halves answer different questions. :mod:`planetary` screens — it reads
already-corrected L2A imagery inside the AOI to find out which overpasses were
actually clear over the water, because tile-level cloud cover cannot tell you
that. :mod:`cdse` then fetches the L1C bundles for the dates that survived,
because the retrieval runs on L1C through ACOLITE, not on Sen2Cor's L2A.

    from oceanstream.coastal.acquire import best_dates, screen_dates
    from oceanstream.coastal.acquire import download_scene, search_scenes

    verdicts = screen_dates(bbox, "2026-06-01", "2026-07-31")
    for pick in best_dates(verdicts, n=2):
        found = search_scenes(bbox=bbox, start=pick.date, end=pick.date)
        download_scene(found[0], "./l1c")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    # CDSE search + download
    "CDSECredentials",
    "SceneCandidate",
    "get_access_token",
    "search_scenes",
    "rank_by_cloud",
    "one_per_date",
    "download_scene",
    "iter_download",
    # Planetary Computer screening
    "DateVerdict",
    "screen_dates",
    "best_dates",
    "measure_over_water_red",
    "open_catalog",
]

if TYPE_CHECKING:
    from oceanstream.coastal.acquire.cdse import (
        CDSECredentials,
        SceneCandidate,
        download_scene,
        get_access_token,
        iter_download,
        one_per_date,
        rank_by_cloud,
        search_scenes,
    )
    from oceanstream.coastal.acquire.planetary import (
        DateVerdict,
        best_dates,
        measure_over_water_red,
        open_catalog,
        screen_dates,
    )

_CDSE_EXPORTS = frozenset(
    {
        "CDSECredentials",
        "SceneCandidate",
        "get_access_token",
        "search_scenes",
        "rank_by_cloud",
        "one_per_date",
        "download_scene",
        "iter_download",
    }
)


def __getattr__(name: str) -> Any:
    """Import on first use, so requests/pystac stay genuinely optional."""
    if name in _CDSE_EXPORTS:
        from oceanstream.coastal.acquire import cdse

        return getattr(cdse, name)
    if name in __all__:
        from oceanstream.coastal.acquire import planetary

        return getattr(planetary, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
