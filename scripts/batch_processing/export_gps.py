"""Export GPS location data from PostgresDB to a local JSON file.

Run this once before batch processing to eliminate the database dependency
from the main pipeline script.

Usage:
    python export_gps.py --cruise-id SD_TPOS2023 --output gps_data.json
    python export_gps.py --cruise-id SD_TPOS2023 --output gps_data.json \
                         --start 2023-06-22 --end 2023-08-01
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def export_gps_data(
    cruise_id: str,
    output_path: Path,
    start_datetime: datetime | None = None,
    end_datetime: datetime | None = None,
    source_container: str = "processed",
) -> Path:
    """Query PostgresDB for file records with GPS location data and save to JSON.

    The output format matches the ``files_list`` structure from
    ``saildrone.process.get_files_list()``: a list of ``(source_path, file_record)``
    tuples where ``file_record`` contains ``file_name``, ``file_freqs``,
    ``file_start_time``, ``file_end_time``, ``location_data``, and ``id``.

    Parameters
    ----------
    cruise_id : str
        Cruise/survey identifier (e.g. ``"SD_TPOS2023"``).
    output_path : Path
        Where to write the JSON file.
    start_datetime, end_datetime : datetime, optional
        Date range filter.
    source_container : str
        Azure blob container name (used only for path construction).

    Returns
    -------
    Path
        The written JSON file path.
    """
    from saildrone.process import get_files_list

    files_list = get_files_list(
        cruise_id=cruise_id,
        source_container=source_container,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )

    logger.info("Fetched %d file records from database", len(files_list))

    # Serialize to JSON-safe format
    records = []
    for source_path, file_record in files_list:
        rec = {
            "source_path": str(source_path),
            "file_name": file_record.get("file_name"),
            "file_freqs": file_record.get("file_freqs"),
            "file_start_time": file_record.get("file_start_time"),
            "file_end_time": file_record.get("file_end_time"),
            "id": file_record.get("id"),
            "location_data": file_record.get("location_data", []),
        }
        # Ensure datetime fields are strings
        for key in ("file_start_time", "file_end_time"):
            val = rec[key]
            if isinstance(val, datetime):
                rec[key] = val.isoformat()
        records.append(rec)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, indent=2, default=str))

    logger.info("Wrote %d records to %s", len(records), output_path)
    return output_path


def load_gps_data(gps_file: Path) -> list[tuple[str, dict]]:
    """Load GPS data from a previously exported JSON file.

    Returns the same ``(source_path, file_record)`` format expected
    by the processing pipeline.
    """
    raw = json.loads(Path(gps_file).read_text())
    result = []
    for rec in raw:
        source_path = rec["source_path"]
        file_record = {
            "file_name": rec["file_name"],
            "file_freqs": rec["file_freqs"],
            "file_start_time": rec["file_start_time"],
            "file_end_time": rec["file_end_time"],
            "id": rec["id"],
            "location_data": rec.get("location_data", []),
        }
        result.append((source_path, file_record))
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Export GPS location data from PostgresDB to JSON"
    )
    parser.add_argument(
        "--cruise-id", default="SD_TPOS2023", help="Cruise identifier"
    )
    parser.add_argument(
        "--output", "-o", default="gps_data.json", help="Output JSON file path"
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--container", default="processed", help="Source container name"
    )

    args = parser.parse_args()

    start_dt = datetime.fromisoformat(args.start) if args.start else None
    end_dt = datetime.fromisoformat(args.end) if args.end else None

    export_gps_data(
        cruise_id=args.cruise_id,
        output_path=Path(args.output),
        start_datetime=start_dt,
        end_datetime=end_dt,
        source_container=args.container,
    )


if __name__ == "__main__":
    main()
