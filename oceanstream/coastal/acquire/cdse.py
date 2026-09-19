"""Copernicus Data Space Ecosystem — search and download Sentinel-2 bundles.

Convenience only. In production, scene discovery belongs to EarthStudio
(``pineviewlabs/sar-watch``), which already handles S2 L1C/L2A across CDSE and
Planetary Computer, Pléiades Neo DIMAP bundles, and per-AOI provider
configuration. This module exists so the library can be driven standalone —
from a shell, from a test, from a notebook — without standing up that stack.

Why OData rather than STAC
--------------------------
CDSE publishes both. OData is the smaller surface that reaches a downloadable
``.zip``: password grant → search → ``/$value``. The STAC endpoint does not
serve the whole SAFE bundle, and ACOLITE wants the bundle.

Searching by polygon, not by tile
---------------------------------
The prototype required an MGRS tile ID (``T29SMC``), which is fine for one
known site and useless for onboarding a new one — nobody knows offhand which
tile covers Loch Eriboll, and a coastal AOI frequently straddles two. Search
here is driven by the AOI footprint via ``OData.CSC.Intersects``; the tile
falls out of the result rather than being an input.

Cloud cover is a weak ranking signal
------------------------------------
``cloudCover`` describes a 110 x 110 km tile. A coastal AOI is a rounding error
inside that, and the correlation between tile cloud and AOI clarity is poor in
exactly the places that need it most — the Scottish and Irish AOIs are
routinely 60-80% cloudy tile-wide with clear coastal strips. So this module
ranks by cloud but does not choose: it returns every candidate and leaves
selection to :mod:`oceanstream.coastal.acquire.planetary`, which measures
over-water reflectance inside the AOI itself.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD_URL = "https://download.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"

#: CDSE access tokens live ten minutes. An L1C bundle is ~800 MB and a slow
#: link can outlast that, so the token is minted immediately before the
#: transfer starts rather than reused from the search.
TOKEN_TTL_S = 600

#: A truncated SAFE bundle is worse than no bundle: ACOLITE fails deep inside
#: a run with an unhelpful error. Anything below this is treated as partial.
MIN_PLAUSIBLE_L1C_BYTES = 100 * 1024 * 1024


def _require_requests() -> Any:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "CDSE acquisition needs requests. Install the acquire extra:\n"
            '    pip install "oceanstream[coastal-acquire]"'
        ) from exc
    return requests


@dataclass(frozen=True)
class CDSECredentials:
    """A CDSE username and password.

    Kept as an explicit object rather than read from the environment at the
    point of use, so that a library caller can supply credentials without
    mutating the process environment.
    """

    username: str
    password: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> CDSECredentials:
        source = os.environ if env is None else env
        username = source.get("CDSE_USERNAME")
        password = source.get("CDSE_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "CDSE_USERNAME and CDSE_PASSWORD are not set. Register free at "
                "https://dataspace.copernicus.eu/ and export them, or pass "
                "CDSECredentials(...) explicitly."
            )
        return cls(username=username, password=password)


@dataclass(frozen=True)
class SceneCandidate:
    """One CDSE product matching a search.

    ``cloud_cover_pct`` is tile-wide and should not be read as AOI clarity —
    see the module docstring.
    """

    product_id: str
    name: str
    sensing_datetime: datetime
    cloud_cover_pct: float
    size_bytes: int
    online: bool = True
    tile: str | None = None
    platform: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def sensing_date(self) -> date:
        return self.sensing_datetime.date()

    @property
    def size_mb(self) -> int:
        return self.size_bytes // (1024 * 1024)


def get_access_token(credentials: CDSECredentials, timeout_s: float = 30.0) -> str:
    """Exchange username/password for a short-lived bearer token."""
    requests = _require_requests()
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": "cdse-public",
            "username": credentials.username,
            "password": credentials.password,
            "grant_type": "password",
        },
        timeout=timeout_s,
    )
    if response.status_code in (400, 401):
        raise RuntimeError(
            "CDSE rejected the credentials. Check CDSE_USERNAME / CDSE_PASSWORD, "
            "and note that accounts with two-factor authentication enabled cannot "
            "use the password grant."
        )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _attribute(product: dict[str, Any], name: str) -> Any:
    for attribute in product.get("Attributes", []):
        if attribute.get("Name") == name:
            return attribute.get("Value")
    return None


_MGRS_PREFIX = "_T"


def _tile_from_name(name: str) -> str | None:
    """MGRS tile out of a SAFE product name, e.g. ``..._T29SMC_...``."""
    for part in name.split("_"):
        if (
            len(part) == 6
            and part[0] == "T"
            and part[1:3].isdigit()
            and part[3:].isalpha()
            and part[3:].isupper()
        ):
            return part
    return None


def _candidate_from_product(product: dict[str, Any]) -> SceneCandidate:
    name = str(product.get("Name", ""))
    raw_start = str(product.get("ContentDate", {}).get("Start", ""))
    try:
        sensing = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
    except ValueError:
        sensing = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
    try:
        cloud = float(_attribute(product, "cloudCover") or 100.0)
    except (TypeError, ValueError):
        cloud = 100.0
    return SceneCandidate(
        product_id=str(product.get("Id", "")),
        name=name,
        sensing_datetime=sensing,
        cloud_cover_pct=cloud,
        size_bytes=int(product.get("ContentLength") or 0),
        online=bool(product.get("Online", True)),
        tile=_tile_from_name(name),
        platform=str(_attribute(product, "platformSerialIdentifier") or "") or None,
        attributes={"Name": name},
    )


def _polygon_wkt(bbox: BBox) -> str:
    west, south, east, north = bbox
    ring = [
        (west, south),
        (east, south),
        (east, north),
        (west, north),
        (west, south),
    ]
    coords = ",".join(f"{lon} {lat}" for lon, lat in ring)
    return f"POLYGON(({coords}))"


def search_scenes(
    *,
    bbox: BBox | None = None,
    tile: str | None = None,
    start: date | str,
    end: date | str,
    product_type: str = "S2MSI1C",
    max_cloud_pct: float | None = None,
    credentials: CDSECredentials | None = None,
    token: str | None = None,
    limit: int = 200,
    timeout_s: float = 90.0,
) -> list[SceneCandidate]:
    """Sentinel-2 products intersecting ``bbox`` (or matching ``tile``).

    Exactly one of ``bbox`` or ``tile`` is required. ``end`` is inclusive of
    the whole day, because a caller writing ``end=2026-07-09`` means that
    date's imagery, not midnight at its start.
    """
    if (bbox is None) == (tile is None):
        raise ValueError(
            "Pass exactly one of bbox= (search by AOI footprint) or tile= "
            "(search by known MGRS tile)."
        )
    requests = _require_requests()
    if token is None:
        token = get_access_token(credentials or CDSECredentials.from_env())

    start_iso = f"{date.fromisoformat(str(start)).isoformat()}T00:00:00.000Z"
    end_iso = f"{date.fromisoformat(str(end)).isoformat()}T23:59:59.999Z"

    clauses = [
        "Collection/Name eq 'SENTINEL-2'",
        (
            "Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' "
            f"and a/OData.CSC.StringAttribute/Value eq '{product_type}')"
        ),
        f"ContentDate/Start ge {start_iso}",
        f"ContentDate/Start le {end_iso}",
    ]
    if tile is not None:
        clauses.append(f"contains(Name,'{tile}')")
    else:
        assert bbox is not None
        clauses.append(
            f"OData.CSC.Intersects(area=geography'SRID=4326;{_polygon_wkt(bbox)}')"
        )
    if max_cloud_pct is not None:
        clauses.append(
            "Attributes/OData.CSC.DoubleAttribute/any(a:a/Name eq 'cloudCover' "
            f"and a/OData.CSC.DoubleAttribute/Value le {max_cloud_pct})"
        )

    response = requests.get(
        ODATA_URL,
        params={
            "$filter": " and ".join(clauses),
            "$orderby": "ContentDate/Start asc",
            "$top": str(min(limit, 1000)),
            "$expand": "Attributes",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout_s,
    )
    response.raise_for_status()
    products = response.json().get("value", [])
    logger.info(
        "CDSE: %d %s product(s) between %s and %s", len(products), product_type, start, end
    )
    return [_candidate_from_product(p) for p in products]


def rank_by_cloud(candidates: list[SceneCandidate]) -> list[SceneCandidate]:
    """Least-cloudy first. A ranking, not a decision — see the module docstring."""
    return sorted(candidates, key=lambda c: (c.cloud_cover_pct, c.sensing_datetime))


def one_per_date(candidates: list[SceneCandidate]) -> list[SceneCandidate]:
    """Collapse to the least-cloudy product per calendar date.

    An AOI that straddles two MGRS tiles returns two products for one overpass,
    and both S2A and S2B can appear on the same date at high latitude — which
    is normal in the Scottish AOIs, not an anomaly.
    """
    best: dict[date, SceneCandidate] = {}
    for candidate in rank_by_cloud(candidates):
        best.setdefault(candidate.sensing_date, candidate)
    return sorted(best.values(), key=lambda c: c.sensing_datetime)


def download_scene(
    candidate: SceneCandidate,
    dest_dir: str | Path,
    *,
    credentials: CDSECredentials | None = None,
    timeout_s: tuple[float, float] = (30.0, 900.0),
    chunk_bytes: int = 16 * 1024 * 1024,
) -> Path:
    """Stream a product bundle to ``dest_dir``. Returns the local path.

    Mints its own token rather than accepting one: a token obtained during
    search may already be close to its ten-minute expiry, and an 800 MB
    transfer that dies at 90% is expensive to discover.

    Writes to a ``.part`` file and renames on completion, so an interrupted
    transfer cannot be mistaken for a complete bundle on the next run.
    """
    if not candidate.online:
        raise RuntimeError(
            f"{candidate.name} is archived offline in CDSE and needs to be ordered "
            "before it can be downloaded. Pick another date, or request it via the "
            "CDSE web interface first."
        )
    requests = _require_requests()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{candidate.name}.zip"
    if target.exists() and target.stat().st_size >= MIN_PLAUSIBLE_L1C_BYTES:
        logger.info(
            "already downloaded: %s (%d MB)",
            target.name,
            target.stat().st_size // (1024 * 1024),
        )
        return target

    token = get_access_token(credentials or CDSECredentials.from_env())
    url = DOWNLOAD_URL.format(product_id=candidate.product_id)
    partial = target.with_suffix(".zip.part")
    logger.info("downloading %s (%d MB)", candidate.name, candidate.size_mb)
    with requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
        allow_redirects=True,
        timeout=timeout_s,
    ) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", "0"))
        written = 0
        next_report = 10
        with partial.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=chunk_bytes):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                if total:
                    pct = 100.0 * written / total
                    if pct >= next_report:
                        logger.info(
                            "  %d%% (%d/%d MB)",
                            int(pct),
                            written // (1024 * 1024),
                            total // (1024 * 1024),
                        )
                        next_report += 10

    if written < MIN_PLAUSIBLE_L1C_BYTES:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"{candidate.name} downloaded only {written // (1024 * 1024)} MB, which is "
            "too small to be a complete SAFE bundle. The transfer was truncated; "
            "retry, or check whether the CDSE token expired mid-download."
        )
    partial.replace(target)
    logger.info("wrote %s (%d MB)", target, written // (1024 * 1024))
    return target


def iter_download(
    candidates: list[SceneCandidate],
    dest_dir: str | Path,
    *,
    credentials: CDSECredentials | None = None,
) -> Iterator[tuple[SceneCandidate, Path | None, str | None]]:
    """Download several products, yielding ``(candidate, path, error)``.

    One unavailable product should not abandon a multi-AOI acquisition run, so
    failures are yielded rather than raised.
    """
    for candidate in candidates:
        try:
            yield candidate, download_scene(candidate, dest_dir, credentials=credentials), None
        except Exception as exc:  # noqa: BLE001 - reported per item, not swallowed
            logger.warning("%s failed: %s", candidate.name, exc)
            yield candidate, None, str(exc)
