"""Rebuilding a Collection keeps the campaign's metadata."""

from __future__ import annotations

import json
from pathlib import Path

from oceanstream.stac.publish import kept_metadata, publish
from oceanstream.stac.tracks import track_features


def _item(day: str, geometry: dict | None = None) -> dict:
    return {
        "type": "Feature", "id": day, "bbox": [1.0, 2.0, 3.0, 4.0], "geometry": geometry,
        "properties": {"oceanstream:day": day, "start_datetime": f"{day}T00:00:00Z",
                       "end_datetime": f"{day}T23:59:00Z", "oceanstream:frequencies": [38000.0]},
        "assets": {},
    }


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "products" / "X"
    for day in ("2022-06-21", "2022-06-22"):
        (root / day).mkdir(parents=True)
        (root / day / "item.json").write_text(json.dumps(_item(day)))
    return root


META = {"title": "X (R/V Test)", "license": "CC0-1.0", "keywords": ["acoustics"],
        "providers": [{"name": "NOAA NCEI", "roles": ["host"]}],
        "oceanstream:platform": {"name": "Test", "type": "research_vessel"},
        "links": [{"rel": "cite-as", "href": "https://doi.org/10.1/x"}]}


def test_metadata_is_applied_then_kept_on_a_plain_rebuild(tmp_path: Path) -> None:
    root = _root(tmp_path)
    coll = publish(str(root), "X", metadata=META, nasc_export=False)
    assert coll["license"] == "CC0-1.0" and coll["title"] == "X (R/V Test)"
    assert coll["summaries"]["oceanstream:days"] == ["2022-06-21", "2022-06-22"]

    # A later run that passes no metadata (e.g. the Prefect flow) keeps it.
    again = publish(str(root), "X", nasc_export=False)
    assert again["license"] == "CC0-1.0"
    assert again["oceanstream:platform"] == META["oceanstream:platform"]
    rels = [link["rel"] for link in again["links"]]
    assert rels.count("cite-as") == 1 and rels.count("item") == 2


def test_kept_metadata_drops_generated_fields() -> None:
    old = {"type": "Collection", "id": "X", "extent": {}, "summaries": {}, "assets": {},
           "license": "CC0-1.0", "links": [{"rel": "item", "href": "./a"},
                                           {"rel": "related", "href": "https://x"}]}
    assert kept_metadata(old) == {"license": "CC0-1.0",
                                  "links": [{"rel": "related", "href": "https://x"}]}


def test_track_features_skip_items_without_positions() -> None:
    line = {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 2]]}
    items = {"a/item.json": _item("2022-06-21", line), "b/item.json": _item("2022-06-22")}
    coll = {"links": [{"rel": "item", "href": "./a/item.json"},
                      {"rel": "item", "href": "./b/item.json"}]}
    feats = track_features(coll, items.__getitem__, "ncei_x")
    assert len(feats) == 1
    assert feats[0]["properties"] == {
        "date": "2022-06-21", "time_start": "2022-06-21T00:00:00Z",
        "time_end": "2022-06-21T23:59:00Z", "point_count": 3,
        "feature_type": "echodata_track", "campaign_id": "ncei_x"}
