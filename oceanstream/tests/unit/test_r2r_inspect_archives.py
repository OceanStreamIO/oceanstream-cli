from __future__ import annotations

from pathlib import Path
import tarfile

from oceanstream.providers.r2r import R2RProvider
from oceanstream.providers.r2r.r2r_metadata import R2RFileInfo, R2RSensorInfo
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors import register_sensor_processor


def _build_dummy_archive(tmp_path: Path) -> Path:
    """Create a tiny R2R-style tar.gz archive for testing."""

    archive_root = tmp_path / "archive_root"
    data_dir = archive_root / "data"
    data_dir.mkdir(parents=True)

    # Minimal metadata files
    file_info = archive_root / "file-info.txt"
    file_info.write_text("Campaign: FK161229\n")

    bag_info = archive_root / "bag-info.txt"
    bag_info.write_text(
        """Sensor Type: test_sensor
Sensor ID: TEST-001
Description: Test sensor
""",
    )

    # Dummy data file inside data/
    data_file = data_dir / "dummy.txt"
    data_file.write_text("dummy")

    archive_path = tmp_path / "FK161229_test.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(archive_root, arcname="FK161229_test")

    return archive_path


def _test_sensor_processor(
    data_dir: Path,
    file_info: R2RFileInfo,
    sensor_info: R2RSensorInfo,
    provider_id: str,
) -> SensorDescriptor:
    # Basic sanity checks on inputs
    assert data_dir.is_dir()
    assert any(data_dir.iterdir())

    return SensorDescriptor(
        sensor_type=sensor_info.sensor_type or "test_sensor",
        sensor_id=sensor_info.sensor_id or "TEST-001",
        provider_id=provider_id,
        platform_id=file_info.platform or "UNKNOWN_PLATFORM",
        campaign_id=file_info.campaign_id or "UNKNOWN_CAMPAIGN",
        description=sensor_info.description or "",
        metadata={"source": "unit-test"},
    )


def test_inspect_archives_returns_descriptor(tmp_path: Path) -> None:
    # Register a temporary test sensor processor
    register_sensor_processor("test_sensor", _test_sensor_processor)

    archive_path = _build_dummy_archive(tmp_path)
    archives_root = archive_path.parent
    work_root = tmp_path / "work"

    provider = R2RProvider()
    descriptors = provider.inspect_archives(archives_root=archives_root, work_root=work_root)

    # We should get exactly one descriptor for the synthetic archive
    assert len(descriptors) == 1
    desc = descriptors[0]

    assert desc.sensor_type == "test_sensor"
    assert desc.sensor_id == "TEST-001"
    assert desc.campaign_id == "FK161229"
    assert desc.provider_id == provider.name
    assert desc.metadata.get("source") == "unit-test"
