"""Tests for ProcessingResult and PipelineResult dataclasses in echodata.processor.

Pure-logic tests — no echopype dependency. The pipeline step methods are not
tested here; only the data-modelling and serialisation layer.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from oceanstream.echodata.processor import PipelineResult, ProcessingResult


# ---------------------------------------------------------------------------
# ProcessingResult
# ---------------------------------------------------------------------------

class TestProcessingResult:
    """Tests for the per-step result dataclass."""

    def test_defaults(self):
        r = ProcessingResult(step="convert", success=True)
        assert r.message == ""
        assert r.duration_seconds == 0.0
        assert r.metadata == {}
        assert r.output_path is None

    def test_all_fields(self):
        r = ProcessingResult(
            step="calibrate",
            success=False,
            output_path=Path("/out/cal.zarr"),
            message="Calibration failed",
            duration_seconds=12.5,
            metadata={"reason": "missing file"},
        )
        assert r.step == "calibrate"
        assert not r.success
        assert r.output_path == Path("/out/cal.zarr")


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------

class TestPipelineResult:
    """Tests for the aggregate pipeline result."""

    @pytest.fixture()
    def mixed_result(self, tmp_path):
        return PipelineResult(
            campaign_id="TPOS_2023",
            output_dir=tmp_path / "out",
            steps=[
                ProcessingResult(step="convert", success=True, duration_seconds=10.0),
                ProcessingResult(step="calibrate", success=False, duration_seconds=5.0,
                                 message="missing cal file"),
                ProcessingResult(step="compute_sv", success=True, duration_seconds=20.0),
            ],
            start_time=datetime(2023, 8, 1, 12, 0, 0),
            end_time=datetime(2023, 8, 1, 12, 1, 0),
        )

    def test_success_all_pass(self, tmp_path):
        pr = PipelineResult(
            campaign_id="test",
            output_dir=tmp_path,
            steps=[
                ProcessingResult(step="a", success=True),
                ProcessingResult(step="b", success=True),
            ],
        )
        assert pr.success is True

    def test_success_any_fail(self, mixed_result):
        assert mixed_result.success is False

    def test_success_empty_steps(self, tmp_path):
        pr = PipelineResult(campaign_id="x", output_dir=tmp_path)
        # No steps → vacuously true
        assert pr.success is True

    def test_total_duration(self, mixed_result):
        assert mixed_result.total_duration == pytest.approx(35.0)

    def test_failed_steps(self, mixed_result):
        failed = mixed_result.failed_steps
        assert len(failed) == 1
        assert failed[0].step == "calibrate"

    def test_to_dict_keys(self, mixed_result):
        d = mixed_result.to_dict()
        assert d["campaign_id"] == "TPOS_2023"
        assert d["success"] is False
        assert d["total_duration_seconds"] == pytest.approx(35.0)
        assert len(d["steps"]) == 3
        assert d["start_time"] == "2023-08-01T12:00:00"

    def test_to_dict_step_structure(self, mixed_result):
        step = mixed_result.to_dict()["steps"][1]
        assert step["step"] == "calibrate"
        assert step["success"] is False
        assert step["message"] == "missing cal file"

    def test_to_dict_none_times(self, tmp_path):
        pr = PipelineResult(campaign_id="x", output_dir=tmp_path)
        d = pr.to_dict()
        assert d["start_time"] is None
        assert d["end_time"] is None

    def test_save_report_json_roundtrip(self, mixed_result, tmp_path):
        report_path = tmp_path / "report.json"
        mixed_result.save_report(report_path)
        assert report_path.exists()

        with open(report_path) as f:
            data = json.load(f)

        assert data["campaign_id"] == "TPOS_2023"
        assert len(data["steps"]) == 3
        assert data["total_duration_seconds"] == pytest.approx(35.0)
