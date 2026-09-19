"""Unit coverage for :mod:`oceanstream.coastal.acquire.cdse`.

The prototype these functions come from had no tests at all, and the two
things most worth pinning down are the ones a live run would not surface
quickly: the OData filter is a string built by concatenation, where a wrong
operator returns a plausible-looking but wrong set of scenes; and a truncated
download produces a file that *looks* like a bundle until ACOLITE opens it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from oceanstream.coastal.acquire.cdse import (
    MIN_PLAUSIBLE_L1C_BYTES,
    CDSECredentials,
    SceneCandidate,
    _candidate_from_product,
    _polygon_wkt,
    _tile_from_name,
    download_scene,
    one_per_date,
    rank_by_cloud,
    search_scenes,
)

SESIMBRA = (-9.15, 38.39, -8.90, 38.51)

# Captured shape of a CDSE OData product record. Trimmed to the fields the
# adapter reads, but the key names and nesting are verbatim.
LIVE_PRODUCT = {
    "Id": "f1c2a3b4-0000-0000-0000-000000000001",
    "Name": "S2C_MSIL1C_20260627T112121_N0511_R037_T29SMC_20260627T131502.SAFE",
    "ContentDate": {"Start": "2026-06-27T11:21:21.024Z", "End": "2026-06-27T11:21:21.024Z"},
    "ContentLength": 780_000_000,
    "Online": True,
    "Attributes": [
        {"Name": "cloudCover", "Value": 4.21},
        {"Name": "platformSerialIdentifier", "Value": "C"},
        {"Name": "productType", "Value": "S2MSI1C"},
    ],
}


def _product(**overrides):
    merged = {**LIVE_PRODUCT, **overrides}
    return merged


def _candidate(name="S2A_MSIL1C_20260601T112121_x_x_T29SMC_x.SAFE", cloud=10.0, day=1, **kw):
    return SceneCandidate(
        product_id=f"id-{day}-{cloud}",
        name=name,
        sensing_datetime=datetime(2026, 6, day, 11, 21, tzinfo=UTC),
        cloud_cover_pct=cloud,
        size_bytes=800_000_000,
        **kw,
    )


class _FakeResponse:
    def __init__(self, *, json_data=None, chunks=None, headers=None, status=200):
        self._json = json_data or {}
        self._chunks = chunks or []
        self.headers = headers or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeRequests:
    """Records every call so the request itself can be asserted on."""

    def __init__(self, *, search=None, chunks=None, headers=None):
        self.calls: list[dict] = []
        self._search = search if search is not None else {"value": []}
        self._chunks = chunks or []
        self._headers = headers or {}

    def post(self, url, data=None, timeout=None):
        self.calls.append({"verb": "POST", "url": url, "data": data})
        return _FakeResponse(json_data={"access_token": "tok"})

    def get(
        self,
        url,
        params=None,
        headers=None,
        timeout=None,
        stream=False,
        allow_redirects=False,
    ):
        self.calls.append({"verb": "GET", "url": url, "params": params or {}})
        if stream:
            return _FakeResponse(chunks=self._chunks, headers=self._headers)
        return _FakeResponse(json_data=self._search)


@pytest.fixture
def fake_requests(monkeypatch):
    def install(**kwargs):
        fake = _FakeRequests(**kwargs)
        monkeypatch.setattr(
            "oceanstream.coastal.acquire.cdse._require_requests", lambda: fake
        )
        return fake

    return install


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("CDSE_USERNAME", "user")
    monkeypatch.setenv("CDSE_PASSWORD", "pass")


class TestCredentials:
    def test_missing_credentials_say_where_to_get_them(self, monkeypatch):
        monkeypatch.delenv("CDSE_USERNAME", raising=False)
        monkeypatch.delenv("CDSE_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="dataspace.copernicus.eu"):
            CDSECredentials.from_env()

    def test_explicit_env_mapping_does_not_touch_the_process(self):
        creds = CDSECredentials.from_env({"CDSE_USERNAME": "a", "CDSE_PASSWORD": "b"})
        assert (creds.username, creds.password) == ("a", "b")


class TestSearchQuery:
    def test_requires_exactly_one_of_bbox_or_tile(self):
        with pytest.raises(ValueError, match="exactly one"):
            search_scenes(start="2026-06-01", end="2026-06-30")
        with pytest.raises(ValueError, match="exactly one"):
            search_scenes(bbox=SESIMBRA, tile="T29SMC", start="2026-06-01", end="2026-06-30")

    def test_bbox_search_uses_an_intersects_polygon(self, fake_requests):
        fake = fake_requests()
        search_scenes(bbox=SESIMBRA, start="2026-06-01", end="2026-06-30")
        query = fake.calls[-1]["params"]["$filter"]
        assert "OData.CSC.Intersects" in query
        assert "SRID=4326" in query
        # A closed ring, longitude first.
        assert "-9.15 38.39,-8.9 38.39,-8.9 38.51,-9.15 38.51,-9.15 38.39" in query

    def test_end_date_covers_the_whole_day(self, fake_requests):
        """``end=2026-06-30`` means that day's imagery, not midnight at its start."""
        fake = fake_requests()
        search_scenes(bbox=SESIMBRA, start="2026-06-01", end="2026-06-30")
        query = fake.calls[-1]["params"]["$filter"]
        assert "ContentDate/Start le 2026-06-30T23:59:59.999Z" in query

    def test_product_type_is_filtered_server_side(self, fake_requests):
        fake = fake_requests()
        search_scenes(bbox=SESIMBRA, start="2026-06-01", end="2026-06-30")
        assert "'S2MSI1C'" in fake.calls[-1]["params"]["$filter"]

    def test_tile_search_skips_the_geometry_clause(self, fake_requests):
        fake = fake_requests()
        search_scenes(tile="T29SMC", start="2026-06-01", end="2026-06-30")
        query = fake.calls[-1]["params"]["$filter"]
        assert "contains(Name,'T29SMC')" in query
        assert "Intersects" not in query

    def test_cloud_filter_is_omitted_unless_asked_for(self, fake_requests):
        fake = fake_requests()
        search_scenes(bbox=SESIMBRA, start="2026-06-01", end="2026-06-30")
        assert "cloudCover" not in fake.calls[-1]["params"]["$filter"]
        search_scenes(bbox=SESIMBRA, start="2026-06-01", end="2026-06-30", max_cloud_pct=30)
        assert "cloudCover" in fake.calls[-1]["params"]["$filter"]


class TestProductParsing:
    def test_parses_the_live_record(self, fake_requests):
        fake_requests(search={"value": [_product()]})
        found = search_scenes(bbox=SESIMBRA, start="2026-06-27", end="2026-06-27")
        assert len(found) == 1
        scene = found[0]
        assert scene.tile == "T29SMC"
        assert scene.cloud_cover_pct == pytest.approx(4.21)
        assert scene.sensing_date.isoformat() == "2026-06-27"
        assert scene.size_mb == 743

    def test_missing_cloud_attribute_sorts_last_rather_than_first(self):
        """An unknown cloud cover must not masquerade as a perfectly clear scene."""
        scene = _candidate_from_product(_product(Attributes=[]))
        assert scene.cloud_cover_pct == 100.0

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("S2C_MSIL1C_20260627T112121_N0511_R037_T29SMC_20260627T131502.SAFE", "T29SMC"),
            ("S2A_MSIL2A_20260601T114351_N0500_R123_T30UVF_20260601T140012.SAFE", "T30UVF"),
            ("SOMETHING_WITHOUT_A_TILE.SAFE", None),
        ],
    )
    def test_tile_is_read_out_of_the_product_name(self, name, expected):
        assert _tile_from_name(name) == expected

    def test_polygon_ring_closes(self):
        wkt = _polygon_wkt(SESIMBRA)
        coords = wkt[len("POLYGON((") : -2].split(",")
        assert coords[0] == coords[-1]
        assert len(coords) == 5


class TestSelection:
    def test_rank_is_least_cloudy_first(self):
        ranked = rank_by_cloud([_candidate(cloud=40, day=1), _candidate(cloud=5, day=2)])
        assert [c.cloud_cover_pct for c in ranked] == [5, 40]

    def test_one_per_date_keeps_the_clearest_of_a_straddled_overpass(self):
        """Two MGRS tiles for one overpass is normal for a coastal AOI, not a duplicate."""
        collapsed = one_per_date(
            [
                _candidate(cloud=60, day=3, name="..._T29SMC_..."),
                _candidate(cloud=8, day=3, name="..._T29SNC_..."),
                _candidate(cloud=20, day=5),
            ]
        )
        assert len(collapsed) == 2
        assert collapsed[0].cloud_cover_pct == 8

    def test_one_per_date_returns_chronological_order(self):
        collapsed = one_per_date([_candidate(day=9), _candidate(day=2), _candidate(day=5)])
        assert [c.sensing_date.day for c in collapsed] == [2, 5, 9]


class TestDownload:
    def test_offline_product_says_it_must_be_ordered(self, tmp_path):
        with pytest.raises(RuntimeError, match="archived offline"):
            download_scene(_candidate(online=False), tmp_path)

    def test_truncated_transfer_is_rejected_and_leaves_no_file(self, tmp_path, fake_requests):
        """A short .zip looks like a bundle until ACOLITE fails deep inside a run."""
        fake_requests(chunks=[b"x" * 1024], headers={"Content-Length": "800000000"})
        with pytest.raises(RuntimeError, match="truncated"):
            download_scene(_candidate(), tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_complete_transfer_lands_without_a_part_suffix(self, tmp_path, fake_requests):
        fake_requests(chunks=[b"x" * MIN_PLAUSIBLE_L1C_BYTES])
        path = download_scene(_candidate(), tmp_path)
        assert path.suffix == ".zip"
        assert not path.name.endswith(".part")
        assert [p.name for p in tmp_path.iterdir()] == [path.name]

    def test_existing_full_bundle_is_not_refetched(self, tmp_path, fake_requests):
        fake = fake_requests(chunks=[b"x" * MIN_PLAUSIBLE_L1C_BYTES])
        candidate = _candidate()
        target = tmp_path / f"{candidate.name}.zip"
        target.write_bytes(b"x" * MIN_PLAUSIBLE_L1C_BYTES)
        download_scene(candidate, tmp_path)
        assert fake.calls == []

    def test_existing_partial_bundle_is_refetched(self, tmp_path, fake_requests):
        fake = fake_requests(chunks=[b"x" * MIN_PLAUSIBLE_L1C_BYTES])
        candidate = _candidate()
        (tmp_path / f"{candidate.name}.zip").write_bytes(b"x" * 4096)
        download_scene(candidate, tmp_path)
        assert any(c["verb"] == "GET" for c in fake.calls)

    def test_token_is_minted_for_the_transfer_not_reused_from_search(
        self, tmp_path, fake_requests
    ):
        """A search-time token can be near its ten-minute expiry; 800 MB is not."""
        fake = fake_requests(chunks=[b"x" * MIN_PLAUSIBLE_L1C_BYTES])
        download_scene(_candidate(), tmp_path)
        assert fake.calls[0]["verb"] == "POST"
