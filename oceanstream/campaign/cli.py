"""Campaign commands for catalogue cruises: metadata, day lists, tracks, mirrors.

Registered on ``oceanstream campaign`` by :mod:`oceanstream.cli`. A campaign's
directory is ``<campaigns dir>/<id>`` (``--campaigns-dir`` or
``OCEANSTREAM_CAMPAIGNS_DIR``, default ``~/.oceanstream/campaigns``).
"""
# Typer commands take one parameter per option.
# ruff: noqa: PLR0913, PLR0917

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import typer

from . import catalogue

CAMPAIGNS_DIR = typer.Option(
    None, "--campaigns-dir", envvar="OCEANSTREAM_CAMPAIGNS_DIR",
    help="Where campaign directories live (default ~/.oceanstream/campaigns).")


def campaigns_root(campaigns_dir: Path | None) -> Path:
    from oceanstream.geotrack.campaign import get_campaigns_dir  # noqa: PLC0415

    return campaigns_dir.expanduser() if campaigns_dir else get_campaigns_dir()


def load_campaign(campaign_id: str, campaigns_dir: Path | None) -> tuple[Path, dict[str, Any]]:
    path = campaigns_root(campaigns_dir) / campaign_id
    meta = path / "campaign.json"
    if not meta.is_file():
        raise typer.BadParameter(
            f"No campaign {campaign_id} in {path.parent}. Create it with "
            f"`oceanstream campaign create {campaign_id} --from-catalogue <csv>`.")
    return path, json.loads(meta.read_text())


def _site_bucket() -> tuple[str, str]:
    from oceanstream.hpc.site import HpcSite  # noqa: PLC0415

    site = HpcSite.load()
    return site.s3_bucket or "oceanstream-bucket", site.s3_products_prefix


def create_from_catalogue(campaign_id: str, csv_path: Path, catalogue_id: str = "",
                          campaigns_dir: Path | None = None, verbose: bool = False) -> Path:
    """``campaign create <id> --from-catalogue``: campaign.json and the metadata files."""
    from oceanstream.geotrack.campaign import create_campaign  # noqa: PLC0415

    row = catalogue.read_row(csv_path, catalogue_id or campaign_id)
    fields = catalogue.campaign_fields(row)
    fields["catalogue_file"] = str(csv_path.resolve())
    path = create_campaign(campaign_id, fields, verbose=verbose,
                           campaigns_dir=campaigns_dir.expanduser() if campaigns_dir else None)
    bucket, prefix = _site_bucket()
    for f in catalogue.write_metadata(path, campaign_id, row, bucket=bucket,
                                      products_prefix=prefix):
        typer.echo(f"  wrote {f}")
    return path



def metadata_command(
    campaign_id: str,
    csv_path: Path | None = typer.Option(None, "--catalogue",
                                         help="Re-read this catalogue (default: the row "
                                              "kept in campaign.json)."),
    check: bool = typer.Option(False, "--check", help="Only check oceanstream-ingest.json."),
    campaigns_dir: Path | None = CAMPAIGNS_DIR,
) -> None:
    """(Re)write stac-collection.json, oceanstream-ingest.json and summary.md."""
    path, meta = load_campaign(campaign_id, campaigns_dir)
    if check:
        problems = catalogue.check_ingest(json.loads((path / catalogue.INGEST).read_text()))
        for p in problems:
            typer.secho(f"  ✗ {p}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1 if problems else 0)
    if csv_path:
        row = catalogue.read_row(csv_path, meta.get("catalogue", {}).get("cruise_id")
                                 or campaign_id)
    elif meta.get("catalogue"):
        row = meta["catalogue"]
    else:
        raise typer.BadParameter("No catalogue row in campaign.json; give --catalogue.")
    bucket, prefix = _site_bucket()
    for f in catalogue.write_metadata(path, campaign_id, row, bucket=bucket,
                                      products_prefix=prefix, run_config=False):
        typer.echo(f"  wrote {f}")

def days_command(
    campaign_id: str,
    start: str = typer.Option("", help="First day (default: the catalogue's first data day)."),
    end: str = typer.Option("", help="Last day (default: the catalogue's last data day)."),
    write: bool = typer.Option(True, help="Write days.txt and listing.tsv."),
    campaigns_dir: Path | None = CAMPAIGNS_DIR,
) -> None:
    """List the raw files of the campaign's source and write the days to process."""
    from oceanstream.hpc.cruise import RunConfig  # noqa: PLC0415
    from oceanstream.sources import ncei  # noqa: PLC0415

    path, meta = load_campaign(campaign_id, campaigns_dir)
    days_file = path / "days.txt"
    created = not days_file.exists()
    if created:
        days_file.write_text("1970-01-01\n")  # RunConfig needs a day to load
    try:
        cfg = RunConfig.load(path)
    finally:
        if created:
            days_file.unlink()
    row = meta.get("catalogue", {})
    listing = ncei.s3_listing(cfg.source(), anonymous=cfg.opendata)
    days = ncei.list_days(listing, start=start or meta.get("start_date", "")[:10],
                          end=end or meta.get("end_date", "")[:10],
                          shift_hours=cfg.day_shift_hours)
    typer.echo(json.dumps(ncei.summary(days)))
    outside = ncei.list_days(listing, shift_hours=cfg.day_shift_hours)
    dropped = sorted(set(outside) - set(days))
    if dropped:
        typer.echo(f"  outside the cruise dates, left out: {len(dropped)} day(s) "
                   f"({sum(outside[d].files for d in dropped)} files)")
    if write:
        days_file.write_text("".join(f"{d}\n" for d in days))
        (path / "listing.tsv").write_text("day\tfiles\tbytes\n" + "".join(
            f"{d}\t{v.files}\t{v.bytes}\n" for d, v in days.items()))
        typer.echo(f"  wrote {days_file} ({len(days)} days; catalogue says "
                   f"{row.get('days_with_data', '?')})")

def gps_command(
    campaign_id: str,
    source: str = typer.Option(..., "--from",
                               help="erddap:<dataset id>, s3://… or https://… CSV files "
                                    "(Echoview .gps.csv or time/lat/lon), or a local path."),
    start: str = typer.Option("", help="First day (default: campaign start)."),
    end: str = typer.Option("", help="Last day (default: campaign end)."),
    erddap_server: str = typer.Option("", help="ERDDAP base URL (default: NOAA PMEL)."),
    upload: bool = typer.Option(True, help="Upload to the project bucket's GPS prefix."),
    campaigns_dir: Path | None = CAMPAIGNS_DIR,
) -> None:
    """Build the campaign's GPS parquet (for --gps-dir) from a published track."""
    import pandas as pd  # noqa: PLC0415

    from oceanstream.sources import gps  # noqa: PLC0415

    path, meta = load_campaign(campaign_id, campaigns_dir)
    start = start or meta.get("start_date", "")[:10]
    end = end or meta.get("end_date", "")[:10]
    if source.startswith("erddap:"):
        track = gps.erddap_track(source.removeprefix("erddap:"), start, end,
                                 server=erddap_server or gps.PMEL_ERDDAP)
    else:
        track = pd.concat([gps.read_csv(f) for f in _open_sources(source)],
                          ignore_index=True)
        track = gps.normalise(track)
        if start and end:
            day = track.time.dt.strftime("%Y-%m-%d")
            track = track[(day >= start) & (day <= end)].reset_index(drop=True)
    info = gps.describe(track)
    typer.echo(json.dumps(info))
    if not info.get("fixes"):
        raise typer.Exit(1)
    out = gps.write_parquet(track, path / "gps" / f"{campaign_id}.parquet")
    (path / "gps" / "coverage.json").write_text(json.dumps({"source": source, **info},
                                                           indent=1))
    typer.echo(f"  wrote {out}")
    if upload:
        from oceanstream.hpc.site import HpcSite  # noqa: PLC0415
        from oceanstream.sources.mirror import s3_client  # noqa: PLC0415

        site = HpcSite.load()
        key = f"{site.s3_gps_prefix}/{campaign_id}/{out.name}"
        s3_client().upload_file(str(out), site.s3_bucket, key)
        typer.echo(f"  uploaded s3://{site.s3_bucket}/{key}")

def mirror_command(
    campaign_id: str,
    source: str = typer.Option(..., "--from",
                               help="An archive URL (.tar.gz, .tar, .zip), zenodo:<record>, "
                                    "griidc:<dataset submission>, or azure (Saildrone file "
                                    "share)."),
    dest: str = typer.Option("", help="s3://bucket/prefix/ (default: source.uri in "
                                      "hpc-run.toml)."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    workers: int = typer.Option(4),
    campaigns_dir: Path | None = CAMPAIGNS_DIR,
) -> None:
    """Copy the campaign's raw files into the project bucket (resumable)."""
    import logging  # noqa: PLC0415

    from oceanstream.sources import mirror  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # With --dest an archive or Zenodo mirror needs nothing from the campaign directory,
    # so it can run in a Job that has only the code.
    path: Path | None = None
    meta: dict[str, Any] = {}
    if source == "azure" or not dest:
        path, meta = load_campaign(campaign_id, campaigns_dir)
    if not dest and path is not None:
        import tomllib  # noqa: PLC0415

        cfg = tomllib.loads((path / "hpc-run.toml").read_text())
        dest = cfg.get("source", {}).get("uri", "")
    if source == "azure":
        bucket, _ = _site_bucket()
        result = mirror.mirror_azure_raw(campaign_id, meta["start_date"][:10],
                                         meta["end_date"][:10], bucket, workers=workers,
                                         dry_run=dry_run)
    else:
        if not dest.startswith("s3://"):
            raise typer.BadParameter("Give --dest s3://bucket/prefix/ or source.uri.")
        bucket, _, prefix = dest.removeprefix("s3://").partition("/")
        if source.startswith(("zenodo:", "griidc:")):
            files = (mirror.zenodo_files(source.removeprefix("zenodo:"))
                     if source.startswith("zenodo:")
                     else mirror.griidc_files(source.removeprefix("griidc:")))
            result = mirror.mirror_files({n: u for n, (u, _) in files.items()}, bucket,
                                         prefix, sizes={n: s for n, (_, s) in files.items()},
                                         workers=workers, dry_run=dry_run)
        else:
            result = mirror.mirror_archive(source, bucket, prefix, dry_run=dry_run)
    typer.echo(json.dumps(result.__dict__))
    if path is not None:
        with (path / "mirror.log").open("a") as f:
            f.write(json.dumps({"source": source, "dest": dest, **result.__dict__}) + "\n")
    if result.failed:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """Add the catalogue commands to the ``campaign`` app."""
    app.command("metadata")(metadata_command)
    app.command("days")(days_command)
    app.command("gps")(gps_command)
    app.command("mirror")(mirror_command)


def _open_sources(source: str) -> list[Any]:
    """Local paths, https URLs or s3:// prefixes of track CSV files."""
    if source.startswith("s3://noaa-wcsd-pds/"):
        from oceanstream.sources import ncei  # noqa: PLC0415

        # A prefix of CSV files, or one CSV object (listed under its own key as "").
        files = [k for k in ncei.s3_listing(source, anonymous=True)
                 if k.endswith(".csv") or (k == "" and source.endswith(".csv"))]
        out = []
        tmp = Path(tempfile.mkdtemp(prefix="gps-"))
        client = ncei.public_client()
        bucket, _, prefix = source.removeprefix("s3://").partition("/")
        for rel in files:
            key = f"{prefix.rstrip('/')}/{rel}" if rel else prefix
            local = tmp / (rel or Path(prefix).name).replace("/", "_")
            body = client.get_object(Bucket=bucket, Key=key)["Body"]
            with local.open("wb") as f:
                while chunk := body.read(8 << 20):
                    f.write(chunk)
            out.append(local)
        return out
    if source.startswith(("http://", "https://")):
        return [source]
    p = Path(source).expanduser()
    return sorted(p.rglob("*.csv")) if p.is_dir() else [p]
