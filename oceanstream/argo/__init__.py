"""Argo float profiles as auxiliary data for a campaign.

Query Argovis for the profiles inside a campaign's extent, match them to the
vessel track and write GeoParquet::

    from oceanstream.argo import build_argo_dataset, load_track

    ds = build_argo_dataset(bbox, start, end, track=load_track("out/geoparquet/FK161229"))
    ds.to_geoparquet("FK161229_argo_profiles.parquet")

CLI: ``oceanstream campaign argo <campaign_id>``.
"""

from .dataset import (
    ARGO_CITATION,
    ARGO_DOI,
    ARGO_LICENSE,
    LAYOUTS,
    QC_MODES,
    ArgoDataset,
    Track,
    build_argo_dataset,
    expand_bbox,
    load_track,
    track_matchups,
)
from .profiles import ACCEPTED_QC, apply_qc, parse_argovis_profile, variable_major

__all__ = [
    "ACCEPTED_QC",
    "ARGO_CITATION",
    "ARGO_DOI",
    "ARGO_LICENSE",
    "LAYOUTS",
    "QC_MODES",
    "ArgoDataset",
    "Track",
    "apply_qc",
    "build_argo_dataset",
    "expand_bbox",
    "load_track",
    "parse_argovis_profile",
    "track_matchups",
    "variable_major",
]
