"""Argovis v2 REST client for Argo profiles.

Uses only the standard library. Argovis answers without an API key at a lower
rate limit; pass ``api_key`` or set ``ARGOVIS_API_KEY`` for the higher one.

Argovis v2 returns each profile's ``data`` as one array per variable
(``data[i]`` belongs to ``data_info[0][i]``); see
:func:`oceanstream.argo.profiles.parse_argovis_profile` for parsing.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Any

DEFAULT_BASE_URL = "https://argovis-api.colorado.edu"

#: Smallest window a request is split down to when Argovis rejects it as too large.
_MIN_WINDOW = timedelta(hours=6)


class ArgovisError(RuntimeError):
    """An Argovis request failed."""


class ArgovisClient:
    """Minimal synchronous client for the Argovis ``/argo`` endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: int = 120,
        retries: int = 3,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("ARGOVIS_API_KEY")
        self.timeout = timeout
        self.retries = max(1, retries)

    def _get(self, path: str, params: dict[str, str]) -> Any:
        """GET ``path`` and decode JSON. 404 (nothing matched) returns ``[]``."""
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": "oceanstream/0.1", "Accept": "application/json"}
        if self.api_key:
            headers["x-argokey"] = self.api_key
        for attempt in range(self.retries):
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == HTTPStatus.NOT_FOUND:
                    return []
                retryable = (
                    e.code == HTTPStatus.TOO_MANY_REQUESTS
                    or e.code >= HTTPStatus.INTERNAL_SERVER_ERROR
                )
                if retryable and attempt < self.retries - 1:
                    time.sleep(2**attempt)
                    continue
                raise
        raise ArgovisError(f"Argovis request failed after {self.retries} attempts: {url}")

    def search_profiles(  # noqa: PLR0913
        self,
        *,
        polygon: list[list[float]] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        platform: str | None = None,
        profile_id: str | None = None,
        data: Iterable[str] | None = ("all",),
    ) -> list[dict]:
        """Argo profile documents matching the filters.

        ``polygon`` is a closed ``[[lon, lat], ...]`` ring; ``start``/``end``
        are naive UTC; ``data`` names the variables to include (``"all"`` for
        every measured variable with its QC flags), or ``None`` for metadata only.
        """
        params: dict[str, str] = {}
        if profile_id:
            params["id"] = profile_id
        if platform:
            params["platform"] = platform
        if polygon:
            params["polygon"] = json.dumps(polygon)
        if start:
            params["startDate"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if end:
            params["endDate"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        if data:
            params["data"] = ",".join(data)
        result = self._get("/argo", params)
        return result if isinstance(result, list) else []


def bbox_polygons(bbox: list[float]) -> list[list[list[float]]]:
    """Closed lon/lat rings for ``[west, south, east, north]``, split at 180°."""
    west, south, east, north = bbox

    def ring(w: float, e: float) -> list[list[float]]:
        return [[w, south], [e, south], [e, north], [w, north], [w, south]]

    if west <= east:
        return [ring(west, east)]
    return [ring(west, 180.0), ring(-180.0, east)]


def time_windows(start: datetime, end: datetime, days: float) -> list[tuple[datetime, datetime]]:
    """Split ``[start, end)`` into consecutive windows of at most ``days`` days."""
    if end <= start:
        return [(start, end)]
    step = timedelta(days=max(days, 1 / 24))
    out = []
    t = start
    while t < end:
        out.append((t, min(t + step, end)))
        t += step
    return out


def fetch_profiles(  # noqa: PLR0913
    bbox: list[float],
    start: datetime,
    end: datetime,
    *,
    chunk_days: float = 10,
    client: ArgovisClient | None = None,
    progress: Callable[[datetime, datetime, int], None] | None = None,
) -> list[dict]:
    """Every Argo profile (all variables) in a bbox and time window.

    Queries in ``chunk_days`` windows, halves a window when Argovis rejects it
    as too large (HTTP 413), and de-duplicates by profile ``_id``.
    ``progress(window_start, window_end, n_docs)`` is called per request.
    """
    client = client or ArgovisClient()
    seen: dict[str, dict] = {}

    def query(polygon: list[list[float]], w0: datetime, w1: datetime) -> None:
        try:
            docs = client.search_profiles(polygon=polygon, start=w0, end=w1)
        except urllib.error.HTTPError as e:
            if e.code != HTTPStatus.REQUEST_ENTITY_TOO_LARGE or (w1 - w0) <= _MIN_WINDOW:
                raise
            mid = w0 + (w1 - w0) / 2
            query(polygon, w0, mid)
            query(polygon, mid, w1)
            return
        if progress:
            progress(w0, w1, len(docs))
        for doc in docs:
            pid = doc.get("_id")
            if pid and pid not in seen:
                seen[pid] = doc

    for polygon in bbox_polygons(bbox):
        for w0, w1 in time_windows(start, end, chunk_days):
            query(polygon, w0, w1)
    return list(seen.values())
