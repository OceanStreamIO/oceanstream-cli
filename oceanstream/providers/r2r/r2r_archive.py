from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tarfile
from typing import List, Optional


@dataclass
class R2RArchiveLayout:
    """Represents the extracted structure of a single R2R archive."""

    archive_path: Path
    campaign_id: str | None
    extract_dir: Path
    file_info_path: Optional[Path]
    bag_info_path: Optional[Path]
    data_dir: Optional[Path]


def find_r2r_archives(root: Path) -> List[Path]:
    """Return all R2R `.tar.gz` archives under a root directory."""

    return sorted(root.rglob("*.tar.gz"))


def _derive_campaign_id_from_filename(archive_path: Path) -> str | None:
    """Derive campaign identifier from an archive filename.

    This uses a simple heuristic: the portion of the filename (without
    extension) before the first underscore is treated as the campaign
    or cruise identifier.
    """

    stem = archive_path.name
    for suffix in (".tar.gz", ".tgz"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    if "_" in stem:
        candidate = stem.split("_", 1)[0]
    else:
        candidate = stem

    candidate = candidate.strip()
    return candidate or None


def extract_r2r_archive(archive_path: Path, work_root: Path) -> R2RArchiveLayout:
    """Extract an R2R `.tar.gz` archive into a working directory.

    The returned layout object captures the paths to key metadata files
    (file-info.txt, bag-info.txt) and the `data` directory if present.
    """

    if not archive_path.exists():
        raise FileNotFoundError(f"R2R archive not found: {archive_path}")

    work_root.mkdir(parents=True, exist_ok=True)
    extract_dir = work_root / archive_path.stem
    extract_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "r:gz") as tf:
        tf.extractall(extract_dir)

    file_info_path: Optional[Path] = None
    bag_info_path: Optional[Path] = None
    data_dir: Optional[Path] = None

    for path in extract_dir.rglob("*"):
        if path.is_file():
            lower_name = path.name.lower()
            if lower_name == "file-info.txt":
                file_info_path = path
            elif lower_name == "bag-info.txt":
                bag_info_path = path
        elif path.is_dir() and path.name == "data":
            data_dir = path

    campaign_id = _derive_campaign_id_from_filename(archive_path)

    return R2RArchiveLayout(
        archive_path=archive_path,
        campaign_id=campaign_id,
        extract_dir=extract_dir,
        file_info_path=file_info_path,
        bag_info_path=bag_info_path,
        data_dir=data_dir,
    )
