"""Additional tests for r2r_archive module to improve coverage."""
from __future__ import annotations

from pathlib import Path
import tarfile
import pytest

from oceanstream.providers.r2r_archive import (
    find_r2r_archives,
    extract_r2r_archive,
    _derive_campaign_id_from_filename,
    R2RArchiveLayout,
)


def test_find_r2r_archives_empty_directory(tmp_path: Path):
    """Test find_r2r_archives with empty directory."""
    result = find_r2r_archives(tmp_path)
    assert result == []


def test_find_r2r_archives_no_archives(tmp_path: Path):
    """Test find_r2r_archives with non-archive files."""
    (tmp_path / "file.txt").write_text("test")
    (tmp_path / "data.csv").write_text("test")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "data.json").write_text("{}")
    
    result = find_r2r_archives(tmp_path)
    assert result == []


def test_find_r2r_archives_single_archive(tmp_path: Path):
    """Test find_r2r_archives with a single archive."""
    archive = tmp_path / "test.tar.gz"
    archive.write_text("dummy")
    
    result = find_r2r_archives(tmp_path)
    assert len(result) == 1
    assert result[0] == archive


def test_find_r2r_archives_multiple_archives_sorted(tmp_path: Path):
    """Test find_r2r_archives returns sorted list."""
    arch1 = tmp_path / "z_last.tar.gz"
    arch2 = tmp_path / "a_first.tar.gz"
    arch3 = tmp_path / "m_middle.tar.gz"
    
    for arch in [arch1, arch2, arch3]:
        arch.write_text("dummy")
    
    result = find_r2r_archives(tmp_path)
    assert len(result) == 3
    assert result == [arch2, arch3, arch1]  # sorted


def test_find_r2r_archives_recursive(tmp_path: Path):
    """Test find_r2r_archives finds archives in subdirectories."""
    subdir1 = tmp_path / "level1"
    subdir2 = subdir1 / "level2"
    subdir2.mkdir(parents=True)
    
    arch1 = tmp_path / "root.tar.gz"
    arch2 = subdir1 / "mid.tar.gz"
    arch3 = subdir2 / "deep.tar.gz"
    
    for arch in [arch1, arch2, arch3]:
        arch.write_text("dummy")
    
    result = find_r2r_archives(tmp_path)
    assert len(result) == 3
    assert arch1 in result
    assert arch2 in result
    assert arch3 in result


def test_find_r2r_archives_ignores_directories(tmp_path: Path):
    """Test that directories named .tar.gz are ignored."""
    fake_dir = tmp_path / "fake.tar.gz"
    fake_dir.mkdir()
    
    real_archive = tmp_path / "real.tar.gz"
    real_archive.write_text("dummy")
    
    result = find_r2r_archives(tmp_path)
    assert len(result) == 1
    assert result[0] == real_archive


def test_derive_campaign_id_simple():
    """Test campaign ID derivation with simple filename."""
    path = Path("/data/FK161229_test.tar.gz")
    result = _derive_campaign_id_from_filename(path)
    assert result == "FK161229"


def test_derive_campaign_id_tgz():
    """Test campaign ID derivation with .tgz extension."""
    path = Path("/data/AT42_experiment.tgz")
    result = _derive_campaign_id_from_filename(path)
    assert result == "AT42"


def test_derive_campaign_id_no_underscore():
    """Test campaign ID when there's no underscore."""
    path = Path("/data/CRUISE2023.tar.gz")
    result = _derive_campaign_id_from_filename(path)
    assert result == "CRUISE2023"


def test_extract_r2r_archive_basic(tmp_path: Path):
    """Test basic archive extraction."""
    # Create archive with minimal structure
    archive_root = tmp_path / "archive_content"
    archive_root.mkdir()
    
    # Add data directory
    data_dir = archive_root / "data"
    data_dir.mkdir()
    (data_dir / "test.csv").write_text("time,lat,lon\n")
    
    # Add metadata files
    (archive_root / "file-info.txt").write_text("Campaign: TEST123\n")
    (archive_root / "bag-info.txt").write_text("Source: test\n")
    
    # Create archive
    archive_path = tmp_path / "TEST123_archive.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(archive_root, arcname="TEST123_archive")
    
    # Extract
    work_root = tmp_path / "work"
    layout = extract_r2r_archive(archive_path, work_root)
    
    # Verify layout
    assert isinstance(layout, R2RArchiveLayout)
    assert layout.archive_path == archive_path
    assert layout.campaign_id == "TEST123"
    assert layout.extract_dir.exists()
    assert layout.file_info_path is not None
    assert layout.file_info_path.exists()
    assert layout.bag_info_path is not None
    assert layout.bag_info_path.exists()
    assert layout.data_dir is not None
    assert layout.data_dir.exists()
    assert (layout.data_dir / "test.csv").exists()


def test_extract_r2r_archive_missing_file(tmp_path: Path):
    """Test extraction with non-existent archive."""
    archive_path = tmp_path / "nonexistent.tar.gz"
    work_root = tmp_path / "work"
    
    with pytest.raises(FileNotFoundError, match="R2R archive not found"):
        extract_r2r_archive(archive_path, work_root)
