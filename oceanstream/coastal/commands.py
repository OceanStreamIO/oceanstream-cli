"""Typed configuration and serialized products shared by coastal CLI commands."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from oceanstream.coastal import config as configs
from oceanstream.coastal.bathymetry.tide import TideCorrection
from oceanstream.coastal.optics.attenuation import calibrations_from_payload
from oceanstream.coastal.qc.floors import scene_floor_verdict
from oceanstream.coastal.qc.verdicts import combine, verdict


def processor_options(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = json.loads(path.read_text())
    types = {
        "retrieval": ("config", configs.RetrievalConfig),
        "mask": ("mask_config", configs.MaskConfig),
        "bathymetry": ("bathymetry_config", configs.BathymetryConfig),
        "attenuation": ("attenuation_config", configs.AttenuationConfig),
        "qc": ("qc_config", configs.QCConfig),
        "detectability": ("detectability_config", configs.DetectabilityConfig),
        "uncertainty": ("uncertainty_config", configs.UncertaintyConfig),
    }
    unknown = set(data) - set(types)
    if unknown:
        raise ValueError(f"Unknown coastal configuration sections: {sorted(unknown)}")
    return {types[key][0]: types[key][1](**value) for key, value in data.items()}


def read_tide(path: Path | None) -> TideCorrection | None:
    if path is None:
        return None
    data = json.loads(path.read_text())
    if data.get("when"):
        data["when"] = dt.datetime.fromisoformat(data["when"].replace("Z", "+00:00"))
    return TideCorrection(**data)


def region_payload(payload: dict[str, Any], region: str | None) -> dict[str, Any]:
    nested = payload.get("attenuation", payload)
    regions = nested.get("regions")
    if regions:
        if region is None and len(regions) != 1:
            raise ValueError(
                "Multiple calibration regions: supply --region. Available: " + ", ".join(regions)
            )
        key = region or next(iter(regions))
        if key not in regions:
            raise ValueError(f"Unknown region {key}.")
        return dict(regions[key])
    return dict(nested)


def reread_verdict(payload: dict[str, Any], theta: float | None = None) -> dict[str, Any]:
    nested = payload.get("attenuation", payload)
    regions = nested.get("regions", {"scene": nested})
    checks = {}
    for key, row in regions.items():
        bands = calibrations_from_payload(row)
        angle: float | None
        if theta is None and bands:
            angle = next(iter(bands.values())).solar_zenith_deg
        else:
            angle = theta
        floors = scene_floor_verdict(bands, angle)
        previous = row.get("verdict") or payload.get("oceanstream:qc")
        flags = list(floors["flags"])
        if not floors["passed"] and not flags:
            flags.append("attenuation_not_assessable")
        if previous is None:
            flags.append("product_validity_unverified")
        elif previous.get("passed") is not True:
            flags += previous.get("flags") or ["upstream_product_rejected"]
        checks[key] = verdict(flags, floors=floors)
    return combine(checks)
