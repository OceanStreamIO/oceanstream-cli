"""A campaign's metadata, from one row of a cruise catalogue CSV.

The catalogue is the table of raw echosounder cruises (``ek80-raw-cruises.csv``:
one row per cruise, 78 columns such as ``cruise_id``, ``platform_name``,
``institution``, ``dataset_doi``, ``licence``). From a row this module writes,
into the campaign directory:

- ``campaign.json`` fields (platform, dates, bbox, licence, DOI, people), with the
  whole row kept under ``catalogue``;
- ``stac-collection.json``: the descriptive fields of the published STAC
  Collection (title, description, licence, keywords, providers, citation,
  publications, links, and ``oceanstream:platform`` / ``oceanstream:campaign``);
- ``oceanstream-ingest.json``: what the web app's ``/api/ingest/stac-collection``
  call and campaign page need (slug, name, provider, Collection URL, Martin source,
  platform, instrument, and the fields only its campaign form can set today);
- ``summary.md``: the same, for people;
- ``hpc-run.toml``: a starting point for :mod:`oceanstream.hpc.cruise`, when the
  source is one it knows. Processing flags are checked on EDITO before a run.

E-mail addresses stay in ``campaign.json`` and ``summary.md`` only; nothing
published (STAC, ingest) carries them. A ``metadata-overrides.json`` in the
campaign directory is merged last, for corrections the catalogue lacks.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

STAC_METADATA = "stac-collection.json"
INGEST = "oceanstream-ingest.json"
SUMMARY = "summary.md"
OVERRIDES = "metadata-overrides.json"
RUN_CONFIG = "hpc-run.toml"

OCEANSTREAM_PROVIDER = {"name": "OceanStream", "roles": ["processor", "host"],
                        "url": "https://github.com/OceanStreamIO"}
SCIENTIFIC_EXT = "https://stac-extensions.github.io/scientific/v1.0.0/schema.json"

# Web app enums (server/db_models.py): campaign providers and platform types.
WEB_PROVIDERS = {"saildrone", "r2r", "emso", "emodnet", "plocan", "oceanlab", "ooi",
                 "earthstudio", "other"}
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SLUG_RE = re.compile(r"^[a-z0-9_]+$")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s;,\"')]+")


# -- reading ------------------------------------------------------------------------------------
def read_row(csv_path: Path, cruise_id: str) -> dict[str, str]:
    """The catalogue row whose ``cruise_id`` is *cruise_id* (case-insensitive)."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row.get("cruise_id", "").strip().lower() == cruise_id.strip().lower():
            return {k: (v or "").strip() for k, v in row.items()}
    stem = cruise_id.lower()[:4]
    close = [r["cruise_id"] for r in rows if stem in r.get("cruise_id", "").lower()]
    hint = f" Similar: {', '.join(close[:8])}." if close else ""
    raise KeyError(f"No cruise {cruise_id!r} in {csv_path}.{hint}")


def split_list(value: str) -> list[str]:
    return [p.strip() for p in re.split(r";\s*", value) if p.strip()]


def first(value: str) -> str:
    parts = split_list(value)
    return parts[0] if parts else ""


def clean_doi(value: str) -> str:
    m = DOI_RE.search(value.strip().strip('"'))
    return m.group(0).rstrip(".") if m else ""


def urls(value: str) -> list[str]:
    return [u for u in re.split(r"[;\s]+", value) if u.startswith(("http://", "https://"))]


def as_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


# -- deriving -----------------------------------------------------------------------------------
def platform_kind(row: dict[str, str]) -> str:
    """``research_vessel``, ``fishing_vessel``, ``usv``, ``sailbuoy``, ``glider`` or ``other``."""
    t = f"{row.get('platform_type', '')} {row.get('platform_name', '')}".lower()
    if "sailbuoy" in t:
        return "sailbuoy"
    if "glider" in t:
        return "glider"
    if "uncrewed" in t or "usv" in t or "saildrone" in t or "autonomous surface" in t:
        return "usv"
    if "fishing" in t:
        return "fishing_vessel"
    if "vessel" in t or "ship" in t:
        return "research_vessel"
    return "other"


# The web app's PlatformType values for each kind (server/db_models.py).
WEB_PLATFORM_TYPE = {"research_vessel": "RESEARCH_VESSEL", "fishing_vessel": "RESEARCH_VESSEL",
                     "usv": "USV", "sailbuoy": "USV", "glider": "GLIDER", "other": "OTHER"}


def repository_short(row: dict[str, str]) -> str:
    repo = row.get("repository", "").lower()
    for key, short in (("ncei", "ncei"), ("rolling deck", "r2r"), ("r2r", "r2r"),
                       ("zenodo", "zenodo"), ("pangaea", "pangaea"), ("hub ocean", "hubocean"),
                       ("nmdc", "nmdc"), ("csiro", "csiro"), ("griidc", "griidc")):
        if key in repo:
            return short
    return "other"


def web_provider(row: dict[str, str]) -> str:
    """The web app's campaign provider for the row."""
    if platform_kind(row) == "usv" and "saildrone" in row.get("platform_name", "").lower():
        return "saildrone"
    if repository_short(row) == "r2r":
        return "r2r"
    return "other"


def slug(campaign_id: str, row: dict[str, str]) -> str:
    """Web slug ``^[a-z0-9_]+$``: the provider or repository, then the campaign id."""
    provider = web_provider(row)
    prefix = provider if provider != "other" else repository_short(row)
    s = re.sub(r"[^a-z0-9]+", "_", f"{prefix}_{campaign_id}".lower()).strip("_")
    return re.sub(r"_+", "_", s)


def licence(row: dict[str, str]) -> tuple[str, dict[str, str] | None]:
    """(STAC ``license``, a ``license`` link or None) from the catalogue's licence text."""
    text = row.get("licence", "")
    low = text.lower()
    table = [("cc0", "CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
             ("cc by 4.0", "CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/"),
             ("cc-by-4.0", "CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/"),
             ("odc-by", "ODC-By-1.0", "https://opendatacommons.org/licenses/by/1-0/"),
             ("odbl", "ODbL-1.0", "https://opendatacommons.org/licenses/odbl/1-0/")]
    for key, spdx, url in table:
        if key in low:
            return spdx, {"rel": "license", "href": url, "type": "text/html", "title": text}
    if "public domain" in low:
        # US Government works are not under copyright in the US; SPDX has no id for that.
        return "other", {"rel": "license", "type": "text/html", "title": text,
                         "href": "https://www.usa.gov/government-copyright"}
    return "other", None


def publications(row: dict[str, str]) -> list[dict[str, str]]:
    out = []
    for cite in split_list(row.get("publications", "")):
        pub = {"citation": cite}
        doi = clean_doi(cite)
        if doi:
            pub["doi"] = doi
        out.append(pub)
    return out


def frequencies(row: dict[str, str]) -> list[str]:
    return [f.strip() for f in row.get("frequencies_khz", "").split(";") if f.strip()]


def platform_block(row: dict[str, str]) -> dict[str, Any]:
    block = {
        "name": row.get("platform_name", ""),
        "kind": platform_kind(row),
        "type": row.get("platform_type", ""),
        "operator": row.get("platform_operator", "") or first(row.get("institution", "")),
        "details": row.get("platform_details", ""),
        "echosounder": {
            "hardware": row.get("hardware_class", ""),
            "software": row.get("echosounder_software", ""),
            "transceivers": row.get("transceivers", ""),
            "transducers": row.get("transducers", ""),
            "frequencies_khz": frequencies(row),
            "pulse_mode": row.get("pulse_mode", ""),
            "sample_type": row.get("sample_type", ""),
            "transducer_mounting": row.get("transducer_mounting", ""),
            "transducer_depth": row.get("transducer_depth_config", ""),
            "calibration": row.get("calibration", ""),
        },
    }
    return _prune(block)


def campaign_block(row: dict[str, str]) -> dict[str, Any]:
    """People, programme and sampling; no e-mail addresses (this block is published)."""
    return _prune({
        "cruise_id": row.get("cruise_id", ""),
        "chief_scientist": row.get("chief_scientist", ""),
        "contact_scientists": row.get("contact_scientists", ""),
        "institution": row.get("institution", ""),
        "programme": row.get("programme_project", ""),
        "funding": row.get("funding", ""),
        "cruise_type": row.get("cruise_type", ""),
        "departure_port": row.get("departure_port", ""),
        "arrival_port": row.get("arrival_port", ""),
        "start_date": row.get("start_date", ""),
        "end_date": row.get("end_date", ""),
        "region": row.get("region", ""),
        "biological_samples": row.get("biological_samples", ""),
        "other_instruments": row.get("other_instruments", ""),
        "positions": row.get("positions", ""),
        "recording_notes": row.get("recording_notes", ""),
    })


def _prune_value(v: Any) -> Any:
    return _prune(v) if isinstance(v, dict) else v


def _prune(d: dict[str, Any]) -> dict[str, Any]:
    """*d* without empty values, recursively."""
    out = {k: _prune_value(v) for k, v in d.items()}
    return {k: v for k, v in out.items() if v not in ("", None, [], {})}


def title(campaign_id: str, row: dict[str, str]) -> str:
    name = row.get("campaign_title", "") or campaign_id
    platform = row.get("platform_name", "")
    t = name if campaign_id.lower() in name.lower() else f"{campaign_id}: {name}"
    return f"{t} ({platform})" if platform and platform.lower() not in t.lower() else t


def description(campaign_id: str, row: dict[str, str]) -> str:
    parts = [row.get("scientific_objectives", ""), row.get("cruise_description", "")]
    body = " ".join(p for p in parts if p)
    source = row.get("citation", "")
    freqs = ", ".join(frequencies(row))
    lead = (f"Processed echosounder data ({freqs} kHz) from {campaign_id}"
            if freqs else f"Processed echosounder data from {campaign_id}")
    lead += f", {row['platform_name']}" if row.get("platform_name") else ""
    dates = " to ".join(d for d in (row.get("data_first_date") or row.get("start_date"),
                                    row.get("data_last_date") or row.get("end_date")) if d)
    lead += f", {dates}." if dates else "."
    text = (f"{lead} One Item per day, with Sv, denoised, masked and pruned Sv, MVBS and NASC "
            "as Zarr, denoising statistics and echograms, processed by OceanStream.")
    if body:
        text += f" Cruise: {body}"
    if source:
        text += f" Source data: {source}"
    return text


def keywords(row: dict[str, str]) -> list[str]:
    kw = ["echosounder", "Sv", "MVBS", "NASC"]
    hw = row.get("hardware_class", "")
    kw.append("EK80" if "EK80" in hw or "WBT" in hw or not hw else hw.split()[0])
    kw += [first(row.get("region", "")), first(row.get("programme_project", "")),
           row.get("platform_name", ""), row.get("cruise_type", "")]
    kw += [f"{f.split()[0]} kHz" for f in frequencies(row)]
    seen: list[str] = []
    for k in kw:
        if k and k not in seen:
            seen.append(k)
    return seen


def providers(row: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    producer = first(row.get("institution", ""))
    if producer:
        out.append({"name": producer, "roles": ["producer", "licensor"]})
    operator = row.get("platform_operator", "")
    if operator and operator != producer:
        out.append({"name": operator, "roles": ["producer"]})
    repo = row.get("repository", "")
    landing = (urls(row.get("landing_page_url", "")) or urls(row.get("download_https", "")))
    doi = clean_doi(row.get("dataset_doi", ""))
    if repo:
        host: dict[str, Any] = {"name": repo, "roles": ["host"]}
        if doi:
            host["url"] = f"https://doi.org/{doi}"
        elif landing:
            host["url"] = landing[0]
        out.append(host)
    out.append(dict(OCEANSTREAM_PROVIDER))
    return out


def links(row: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    doi = clean_doi(row.get("dataset_doi", ""))
    if doi:
        out.append({"rel": "cite-as", "href": f"https://doi.org/{doi}",
                    "title": "Source dataset (raw echosounder files)"})
    _, lic = licence(row)
    if lic:
        out.append(lic)
    cruise_doi = clean_doi(row.get("cruise_doi", ""))
    if cruise_doi:
        out.append({"rel": "related", "href": f"https://doi.org/{cruise_doi}", "title": "Cruise"})
    for u in urls(row.get("landing_page_url", "")):
        if not doi or doi not in u:
            out.append({"rel": "related", "href": u, "title": "Data landing page"})
    for u in urls(row.get("cruise_report_url", "")):
        out.append({"rel": "related", "href": u, "title": "Cruise report"})
    for u in urls(row.get("homepage_url", "")):
        out.append({"rel": "related", "href": u, "title": "Programme or cruise page"})
    seen: set[str] = set()
    unique = []
    for link in out:
        if link["href"] not in seen:
            seen.add(link["href"])
            unique.append(link)
    return unique


def stac_metadata(campaign_id: str, row: dict[str, str]) -> dict[str, Any]:
    """Descriptive Collection fields for :func:`oceanstream.stac.publish.publish`."""
    lic, _ = licence(row)
    meta: dict[str, Any] = {
        "title": title(campaign_id, row),
        "description": description(campaign_id, row),
        "license": lic,
        "keywords": keywords(row),
        "providers": providers(row),
        "stac_extensions": [SCIENTIFIC_EXT],
        "sci:citation": row.get("citation", ""),
        "sci:publications": publications(row),
        "links": links(row),
        "oceanstream:platform": platform_block(row),
        "oceanstream:campaign": campaign_block(row),
    }
    doi = clean_doi(row.get("dataset_doi", ""))
    if doi:
        meta["sci:doi"] = doi
    return _prune(meta)


def ingest_fields(campaign_id: str, row: dict[str, str], *, bucket: str = "oceanstream-bucket",
                  products_prefix: str = "hpc/products") -> dict[str, Any]:
    """What the web app's ingest call and campaign page need, in one place."""
    s = slug(campaign_id, row)
    kind = platform_kind(row)
    lic, _ = licence(row)
    return _prune({
        "campaignSlug": s,
        "name": title(campaign_id, row),
        "provider": web_provider(row),
        "collectionUrl": f"s3://{bucket}/{products_prefix}/{campaign_id}/collection.json",
        "martinSource": f"{s}_echodata",
        "tiles": f"s3://{bucket}/tiles/{s}_echodata.pmtiles",
        "platform": {
            "name": row.get("platform_name", ""),
            "webPlatformType": WEB_PLATFORM_TYPE[kind],
            "kind": kind,
            "operator": row.get("platform_operator", "") or first(row.get("institution", "")),
        },
        "instrument": {"id": ("acoustic-simrad-ek60"
                              if row.get("hardware_class", "").startswith("EK60")
                              else "acoustic-simrad-ek80"),
                       "manufacturer": "Kongsberg",
                       "model": row.get("hardware_class", "") or "EK80",
                       "frequencies_khz": frequencies(row)},
        "campaignForm": {
            "chiefScientist": row.get("chief_scientist", ""),
            "project": row.get("programme_project", ""),
            "funding": row.get("funding", ""),
            "institution": row.get("institution", ""),
        },
        "license": lic,
        "doi": clean_doi(row.get("dataset_doi", "")),
        "notes": ("The importer creates a platform only from 'R/V <name>' in the title, as a "
                  "research vessel; platform.webPlatformType is what it should be."),
    })


def campaign_fields(row: dict[str, str]) -> dict[str, Any]:
    """Fields for ``campaign.json`` (oceanstream.geotrack.campaign.create_campaign)."""
    lic, _ = licence(row)
    bbox = [as_float(row.get(k, "")) for k in ("lon_min", "lat_min", "lon_max", "lat_max")]
    pid = re.sub(r"[^a-z0-9]+", "-", row.get("platform_name", "").lower()).strip("-")
    fields: dict[str, Any] = {
        "platforms": [{"id": pid, "name": row.get("platform_name", ""),
                       "type": row.get("platform_type", "")}] if pid else [],
        "description": description(row.get("cruise_id", ""), row),
        "start_date": row.get("data_first_date") or row.get("start_date", ""),
        "end_date": row.get("data_last_date") or row.get("end_date", ""),
        "attribution": row.get("citation", ""),
        "license": lic,
        "doi": clean_doi(row.get("dataset_doi", "")),
        "source_repository": row.get("repository", ""),
        "keywords": keywords(row),
        "chief_scientist": row.get("chief_scientist", ""),
        "institution": row.get("institution", ""),
        "project": row.get("programme_project", ""),
        "funding": row.get("funding", ""),
        "contact_emails": row.get("contact_emails", ""),
        "data_contact_email": row.get("data_contact_email", ""),
        "catalogue": row,
    }
    if all(v is not None for v in bbox):
        lon0, lat0, lon1, lat1 = (float(v) for v in bbox if v is not None)
        if -180 <= lon0 < lon1 <= 180 and -90 <= lat0 < lat1 <= 90:  # noqa: PLR2004
            fields["bbox"] = [lon0, lat0, lon1, lat1]
    return _prune(fields)


def summary_markdown(campaign_id: str, row: dict[str, str]) -> str:
    """A readable summary of the catalogue row, for the campaign directory."""
    def line(label: str, key: str) -> str:
        v = row.get(key, "")
        return f"| {label} | {v.replace('|', '/')} |" if v else ""

    rows = [
        line("Campaign", "campaign_title"), line("Programme", "programme_project"),
        line("Platform", "platform_name"), line("Platform type", "platform_type"),
        line("Operator", "platform_operator"), line("Institution", "institution"),
        line("Funding", "funding"), line("Chief scientist", "chief_scientist"),
        line("Contacts", "contact_scientists"), line("Contact e-mail", "contact_emails"),
        line("Data contact", "data_contact_email"), line("Dates", "start_date"),
        line("Data from", "data_first_date"), line("Data to", "data_last_date"),
        line("Days with data", "days_with_data"), line("Region", "region"),
        line("Ports", "departure_port"), line("Repository", "repository"),
        line("Download", "download_url"), line("Access", "access_method"),
        line("Dataset DOI", "dataset_doi"), line("Licence", "licence"),
        line("Echosounder", "transceivers"), line("Transducers", "transducers"),
        line("Frequencies (kHz)", "frequencies_khz"), line("Pulse", "pulse_mode"),
        line("Samples", "sample_type"), line("Ping interval (s)", "ping_interval_s"),
        line("Range (m)", "max_range_m"), line("Positions", "positions"),
        line("Transducer depth", "transducer_depth_config"), line("Calibration", "calibration"),
        line("Raw files", "files_raw"), line("Raw size (GB)", "size_raw_gb"),
        line("Ground truth", "biological_samples"), line("Notes", "notes"),
    ]
    objectives = row.get("scientific_objectives", "")
    pubs = "\n".join(f"- {p}" for p in split_list(row.get("publications", "")))
    out = [f"# {title(campaign_id, row)}", "",
           f"From the cruise catalogue row `{row.get('cruise_id', '')}` "
           f"(last checked {row.get('last_checked', 'unknown')}).", ""]
    if objectives:
        out += [objectives, ""]
    out += ["| Field | Value |", "|---|---|", *[r for r in rows if r], ""]
    if pubs:
        out += ["## Publications", "", pubs, ""]
    if row.get("citation"):
        out += ["## Citation", "", row["citation"], ""]
    return "\n".join(out)


# -- hpc-run.toml starter ----------------------------------------------------------------------
NCEI_RE = re.compile(r"^s3://noaa-wcsd-pds/data/raw/([^/]+)/([^/]+)/([^/]+)/?$")


def depth_offset(row: dict[str, str]) -> float | None:
    """Transducer depth below the surface, from the EK80 configuration or the notes."""
    text = row.get("transducer_depth_config", "")
    m = re.search(r"TransducerOffsetZ ([\d.]+)(?:[–-]([\d.]+))? m", text)
    if m and float(m.group(1)) > 0:
        return float(m.group(1))
    m = re.search(r"depth of ([\d.]+) m", row.get("calibration", "") + row.get("notes", ""))
    return float(m.group(1)) if m else None


def run_config_toml(campaign_id: str, row: dict[str, str]) -> str:
    """A starting ``hpc-run.toml``. Processing flags are guesses until the EDITO test."""
    src = row.get("download_url", "")
    m = NCEI_RE.match(src)
    lines = [f"# {campaign_id}: starting point from the catalogue; check every flag on EDITO "
             "first (docs/hpc-campaigns.md).", "", "[days]", 'file = "days.txt"', "test = []",
             "", "[source]"]
    if m:
        ship, cruise, instrument = m.groups()
        lines += ['kind = "ncei"', f'ship = "{ship}"', f'instrument = "{instrument}"',
                  'layout = "flat"']
        if cruise != campaign_id:
            lines.append(f"# NCEI cruise folder: {cruise}")
    else:
        lines += ['kind = "s3"',
                  f'uri = "s3://oceanstream-bucket/mirror/{campaign_id}/raw/"',
                  'layout = "flat"',
                  f"# mirrored from: {src or row.get('landing_page_url', '')}"]
    lines += ["day_shift_hours = 0", ""]
    positions = row.get("positions", "").lower()
    has_nmea = positions.startswith("nmea in raw file") or "seapath" in positions
    lines += ["[gps]", f"enabled = {'false' if has_nmea else 'true'}"]
    if not has_nmea:
        lines.append("# positions: " + (row.get("positions", "") or "unknown")[:120])
    sample = row.get("sample_type", "").lower()
    encode = "complex" if "complex" in sample and "power" not in sample else (
        "power" if "power" in sample and "complex" not in sample else "auto")
    pulse = row.get("pulse_mode", "").upper()
    waveform = "BB" if pulse.startswith("FM") else "CW"
    args = ["--expected-categories", "short_pulse", "--waveform-mode", waveform,
            "--encode-mode", encode, "--sv-float32", "--sv-range-bin-m", "0.18",
            "--background-estimator", "quantile", "--seabed-mask", "--seabed-line", "edge",
            "--seabed-mask-offset", "5", "--mvbs-ping-time-bin", "60s", "--no-crosstalk",
            "--emit-denoise-diagnostics", "--skip-combined-echograms",
            "--skip-campaign-echograms", "--strict"]
    depth = depth_offset(row)
    if depth:
        args += ["--depth-offset", f"{depth:g}"]
    lines += ["", "[job]", 'mode = "node"', "cpus = 4", "day_memory_gb = 30",
              'walltime = "12:00:00"', "", "[pipeline]", 'preset = ""',
              "args = [", *[f'  "{a}",' for a in args], "]", "",
              "[output]", f'dest = "hpc/products/{campaign_id}"', ""]
    return "\n".join(lines)


# -- writing -----------------------------------------------------------------------------------
def merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Deep merge: dicts merge, everything else in *extra* replaces."""
    out = dict(base)
    for k, v in extra.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def write_metadata(  # noqa: PLR0913
    campaign_dir: Path, campaign_id: str, row: dict[str, str], *,
                   bucket: str = "oceanstream-bucket", products_prefix: str = "hpc/products",
                   run_config: bool = True) -> list[Path]:
    """Write the STAC, ingest and summary files (and a starter hpc-run.toml if absent)."""
    if not CAMPAIGN_ID_RE.match(campaign_id):
        raise ValueError(f"Campaign id {campaign_id!r}: use letters, digits, '_', '.', '-' only; "
                         "it names the S3 container.")
    overrides: dict[str, Any] = {}
    path = campaign_dir / OVERRIDES
    if path.is_file():
        overrides = json.loads(path.read_text())
    stac = merge(stac_metadata(campaign_id, row), overrides.get("stac", {}))
    ingest = merge(ingest_fields(campaign_id, row, bucket=bucket,
                                 products_prefix=products_prefix), overrides.get("ingest", {}))
    if not SLUG_RE.match(ingest["campaignSlug"]):
        raise ValueError(f"Slug {ingest['campaignSlug']!r} does not match {SLUG_RE.pattern}")
    if ingest["provider"] not in WEB_PROVIDERS:
        raise ValueError(f"Provider {ingest['provider']!r} is not one of {sorted(WEB_PROVIDERS)}")
    campaign_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, doc in ((STAC_METADATA, stac), (INGEST, ingest)):
        (campaign_dir / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        written.append(campaign_dir / name)
    summary = summary_markdown(campaign_id, row)
    if overrides.get("notes"):
        summary += "\n## Corrections\n\n" + "\n".join(f"- {n}" for n in overrides["notes"]) + "\n"
    (campaign_dir / SUMMARY).write_text(summary)
    written.append(campaign_dir / SUMMARY)
    if run_config and not (campaign_dir / RUN_CONFIG).exists():
        (campaign_dir / RUN_CONFIG).write_text(run_config_toml(campaign_id, row))
        written.append(campaign_dir / RUN_CONFIG)
    return written


def check_ingest(doc: dict[str, Any]) -> list[str]:
    """Problems that would make the web app's ingest call fail or create a bare campaign."""
    problems = []
    if not SLUG_RE.match(doc.get("campaignSlug", "")):
        problems.append("campaignSlug must match ^[a-z0-9_]+$")
    if doc.get("provider") not in WEB_PROVIDERS:
        problems.append(f"provider must be one of {sorted(WEB_PROVIDERS)}")
    if not doc.get("name"):
        problems.append("name is empty")
    if not str(doc.get("collectionUrl", "")).startswith(("s3://", "https://")):
        problems.append("collectionUrl must be s3:// or https://")
    if not (doc.get("platform") or {}).get("name"):
        problems.append("platform.name is empty")
    return problems


def ids(rows: Iterable[dict[str, str]]) -> list[str]:
    return [r.get("cruise_id", "") for r in rows]
