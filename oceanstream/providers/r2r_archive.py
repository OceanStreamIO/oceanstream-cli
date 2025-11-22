"""Helpers for discovering and extracting R2R archive bundles.

These utilities are **read-only** with respect to the main ingest
pipeline – they do not write GeoParquet or STAC, and are intended to
support sensor catalogue introspection and future ingest wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tarfile
from typing import Iterable


@dataclass
class R2RArchiveLayout:
	"""Describes the extracted structure of a single R2R archive.

	Attributes
	----------
	archive_path:
		Original ``.tar.gz`` archive path.
	campaign_id:
		Campaign identifier derived from filename and/or metadata.
	extract_dir:
		Directory where the archive contents were extracted.
	file_info_path:
		Path to ``file-info.txt`` if found, otherwise ``None``.
	bag_info_path:
		Path to ``bag-info.txt`` if found, otherwise ``None``.
	data_dir:
		Path to the ``data`` directory within the archive, if present.
	"""

	archive_path: Path
	campaign_id: str
	extract_dir: Path
	file_info_path: Path | None
	bag_info_path: Path | None
	data_dir: Path | None


def find_r2r_archives(root: Path) -> list[Path]:
	"""Return all ``*.tar.gz`` R2R archives under ``root``.

	The search is recursive and only returns regular files.
	"""

	archives: list[Path] = []
	for path in root.rglob("*.tar.gz"):
		if path.is_file():
			archives.append(path)
	return sorted(archives)


def _derive_campaign_id_from_filename(archive_path: Path) -> str:
	"""Derive campaign identifier from an archive filename.

	For now we use a simple heuristic: the leading alphanumeric token
	before the first underscore, e.g. ``FK161229`` from
	``FK161229_something.tar.gz``.

	This can be refined later or overridden by metadata from
	``file-info.txt`` once parsed.
	"""

	stem = archive_path.name
	# Strip compression suffixes first
	if stem.endswith(".tar.gz"):
		stem = stem[:-7]
	elif stem.endswith(".tgz"):
		stem = stem[:-4]

	token = stem.split("_")[0]
	# Fallback to full stem if we cannot split
	return token or stem


def extract_r2r_archive(archive_path: Path, work_root: Path) -> R2RArchiveLayout:
	"""Extract an R2R archive into ``work_root`` and locate key files.

	Parameters
	----------
	archive_path:
		Path to the ``.tar.gz`` file.
	work_root:
		Base directory for extraction. A subdirectory named after the
		archive stem will be created inside this directory.
	"""

	if not archive_path.is_file():
		raise FileNotFoundError(f"R2R archive not found: {archive_path}")

	work_root.mkdir(parents=True, exist_ok=True)

	# Create a stable extraction directory name from the archive stem.
	stem = archive_path.name
	if stem.endswith(".tar.gz"):
		stem = stem[:-7]
	elif stem.endswith(".tgz"):
		stem = stem[:-4]

	extract_dir = work_root / stem
	extract_dir.mkdir(parents=True, exist_ok=True)

	# Extract the tarball contents.
	# R2R archives are typically standard POSIX tarballs compressed
	# with gzip.
	with tarfile.open(archive_path, mode="r:gz") as tf:
		tf.extractall(extract_dir)

	# Locate key files and directories.
	file_info_path: Path | None = None
	bag_info_path: Path | None = None
	data_dir: Path | None = None

	for path in extract_dir.rglob("*"):
		if path.is_dir() and path.name == "data" and data_dir is None:
			data_dir = path
		elif path.is_file():
			name_lower = path.name.lower()
			if name_lower == "file-info.txt" and file_info_path is None:
				file_info_path = path
			elif name_lower == "bag-info.txt" and bag_info_path is None:
				bag_info_path = path

	campaign_id = _derive_campaign_id_from_filename(archive_path)

	return R2RArchiveLayout(
		archive_path=archive_path,
		campaign_id=campaign_id,
		extract_dir=extract_dir,
		file_info_path=file_info_path,
		bag_info_path=bag_info_path,
		data_dir=data_dir,
	)

