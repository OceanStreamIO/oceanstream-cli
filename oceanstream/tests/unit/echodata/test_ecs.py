"""Unit tests for oceanstream.echodata.calibrate.ecs module.

Tests the ECS file normalisation, parsing, and ``build_cal_params_from_ecs``
helper.  All echopype interactions are mocked so these tests run without
echopype installed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# _normalize_ecs_text
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeEcsText:
    """Tests for the Echoview header rewriting function."""

    def test_channel_index_rewritten(self):
        from oceanstream.echodata.calibrate.ecs import _normalize_ecs_text

        text = "SourceCal T1 (channel 1)\n  Frequency = 38000\n"
        result = _normalize_ecs_text(text)
        assert "SourceCal T1_C1\n" in result

    def test_multiple_channels(self):
        from oceanstream.echodata.calibrate.ecs import _normalize_ecs_text

        text = (
            "SourceCal T1 (channel 1)\n"
            "  Frequency = 38000\n"
            "SourceCal T1 (channel 2)\n"
            "  Frequency = 200000\n"
        )
        result = _normalize_ecs_text(text)
        assert "SourceCal T1_C1\n" in result
        assert "SourceCal T1_C2\n" in result

    def test_non_channel_annotation_stripped(self):
        from oceanstream.echodata.calibrate.ecs import _normalize_ecs_text

        text = "LocalCal T2 (some annotation)\n"
        result = _normalize_ecs_text(text)
        assert result == "LocalCal T2\n"

    def test_indented_lines_unchanged(self):
        from oceanstream.echodata.calibrate.ecs import _normalize_ecs_text

        text = "  SourceCal T1 (channel 1)\n"
        result = _normalize_ecs_text(text)
        # Leading whitespace → not a header, so unchanged
        assert result == text

    def test_empty_lines_unchanged(self):
        from oceanstream.echodata.calibrate.ecs import _normalize_ecs_text

        text = "\n\n"
        result = _normalize_ecs_text(text)
        assert result == text

    def test_non_header_lines_unchanged(self):
        from oceanstream.echodata.calibrate.ecs import _normalize_ecs_text

        text = "Frequency = 38000\nGain = 25.5\n"
        result = _normalize_ecs_text(text)
        assert result == text

    def test_fileset_header_rewritten(self):
        from oceanstream.echodata.calibrate.ecs import _normalize_ecs_text

        text = "FileSet FS1 (channel 3)\n"
        result = _normalize_ecs_text(text)
        assert "FileSet FS1_C3\n" in result


# ═══════════════════════════════════════════════════════════════════════════
# _coerce_to_path
# ═══════════════════════════════════════════════════════════════════════════


class TestCoerceToPath:
    """Tests for _coerce_to_path helper."""

    def test_path_input_creates_temp(self, tmp_path: Path):
        from oceanstream.echodata.calibrate.ecs import _coerce_to_path

        ecs = tmp_path / "test.ecs"
        ecs.write_text("SourceCal T1 (channel 1)\n  Frequency = 38000\n")

        path, is_temp = _coerce_to_path(ecs)
        try:
            assert is_temp is True
            assert path.exists()
            content = path.read_text()
            assert "T1_C1" in content
        finally:
            path.unlink(missing_ok=True)

    def test_missing_path_raises(self):
        from oceanstream.echodata.calibrate.ecs import _coerce_to_path

        with pytest.raises(FileNotFoundError, match="ECS file not found"):
            _coerce_to_path(Path("/nonexistent/calibration.ecs"))

    def test_string_content_creates_temp(self):
        from oceanstream.echodata.calibrate.ecs import _coerce_to_path

        content = "SourceCal T1 (channel 1)\n  Frequency = 38000\n"
        path, is_temp = _coerce_to_path(content)
        try:
            assert is_temp is True
            assert path.exists()
            assert "T1_C1" in path.read_text()
        finally:
            path.unlink(missing_ok=True)

    def test_empty_string_raises(self):
        from oceanstream.echodata.calibrate.ecs import _coerce_to_path

        with pytest.raises(ValueError, match="ECS content is empty"):
            _coerce_to_path("   ")

    def test_unsupported_type_raises(self):
        from oceanstream.echodata.calibrate.ecs import _coerce_to_path

        with pytest.raises(TypeError, match="Unsupported ECS source type"):
            _coerce_to_path(42)


# ═══════════════════════════════════════════════════════════════════════════
# parse_ecs
# ═══════════════════════════════════════════════════════════════════════════


class TestParseEcs:
    """Tests for parse_ecs function."""

    def test_import_error_raised(self, tmp_path: Path):
        """parse_ecs should raise ImportError when echopype is missing."""
        from oceanstream.echodata.calibrate.ecs import parse_ecs

        ecs_file = tmp_path / "cal.ecs"
        ecs_file.write_text("SourceCal T1\n  Frequency = 38000\n")

        with patch.dict("sys.modules", {"echopype.calibrate.ecs": None}):
            with pytest.raises(ImportError, match="echopype"):
                parse_ecs(ecs_file)

    def test_happy_path(self, tmp_path: Path):
        """parse_ecs returns env/cal/cal_BB from echopype's parser."""
        import oceanstream.echodata.calibrate.ecs as ecs_mod

        # Create a real ECS file on disk (content irrelevant — parser is mocked)
        ecs_file = tmp_path / "cal.ecs"
        ecs_file.write_text("SourceCal T1\n  Frequency = 38000\n")

        sentinel_env = MagicMock(name="env_ds")
        sentinel_cal = MagicMock(name="cal_ds")
        sentinel_bb = MagicMock(name="cal_BB_ds")

        MockParser = MagicMock()
        parser_instance = MagicMock()
        MockParser.return_value = parser_instance
        parser_instance.get_cal_params.return_value = {"dummy": True}

        mock_ev2ep = MagicMock(return_value=(sentinel_env, sentinel_cal, sentinel_bb))

        mock_ecs_module = MagicMock()
        mock_ecs_module.ECSParser = MockParser
        mock_ecs_module.ecs_ev2ep = mock_ev2ep

        with patch.dict("sys.modules", {"echopype.calibrate.ecs": mock_ecs_module}):
            result = ecs_mod.parse_ecs(ecs_file)

        assert result["env"] is sentinel_env
        assert result["cal"] is sentinel_cal
        assert result["cal_BB"] is sentinel_bb

        parser_instance.parse.assert_called_once()
        parser_instance.get_cal_params.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# build_cal_params_from_ecs
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildCalParamsFromEcs:
    """Tests for build_cal_params_from_ecs function."""

    def test_no_sources_raises(self):
        from oceanstream.echodata.calibrate.ecs import build_cal_params_from_ecs

        with pytest.raises(ValueError, match="At least one"):
            build_cal_params_from_ecs(MagicMock(), ecs_short=None, ecs_long=None)

    def test_single_pulse(self):
        """Single-pulse path: one ECS source → aligned → returned."""
        import oceanstream.echodata.calibrate.ecs as ecs_mod

        sentinel_env = MagicMock(name="env_ds")
        sentinel_cal = MagicMock(name="cal_ds")
        mock_parse = MagicMock(return_value={
            "env": sentinel_env,
            "cal": sentinel_cal,
            "cal_BB": None,
        })
        mock_conform = MagicMock(side_effect=lambda ds, freq: ds)
        mock_ds2dict = MagicMock(side_effect=lambda ds: {"key": "value"})

        mock_ed = MagicMock()
        mock_ed.__getitem__ = MagicMock(return_value=MagicMock())

        mock_ecs_module = MagicMock()
        mock_ecs_module.conform_channel_order = mock_conform
        mock_ecs_module.ecs_ds2dict = mock_ds2dict

        with patch.object(ecs_mod, "parse_ecs", mock_parse), \
             patch.dict("sys.modules", {"echopype.calibrate.ecs": mock_ecs_module}):
            env, cal = ecs_mod.build_cal_params_from_ecs(
                mock_ed, ecs_short="SourceCal T1\n  Frequency = 38000\n"
            )

        mock_parse.assert_called_once()
        assert isinstance(env, dict)
        assert isinstance(cal, dict)

    def test_dual_pulse_merge(self):
        """Dual-pulse path: two ECS sources → per-channel merge."""
        import numpy as np
        import xarray as xr

        import oceanstream.echodata.calibrate.ecs as ecs_mod

        mock_detect = MagicMock(return_value=["short", "long"])

        # Build mock datasets with a 'channel' dim and one data var
        def _make_ds(val):
            return xr.Dataset({
                "gain": xr.DataArray([val, val], dims=["channel"]),
            }, coords={"channel": ["ch1", "ch2"]})

        short_env, short_cal = _make_ds(10.0), _make_ds(25.0)
        long_env, long_cal = _make_ds(20.0), _make_ds(30.0)

        mock_parse = MagicMock(side_effect=[
            {"env": short_env, "cal": short_cal, "cal_BB": None},
            {"env": long_env, "cal": long_cal, "cal_BB": None},
        ])

        mock_conform = MagicMock(side_effect=lambda ds, freq: ds)
        mock_ds2dict = MagicMock(side_effect=lambda ds: {
            v: ds[v].values.tolist() for v in ds.data_vars
        })

        mock_ed = MagicMock()
        freq_nominal = xr.DataArray([38000.0, 200000.0], dims=["channel"])
        mock_beam = MagicMock()
        mock_beam.__getitem__ = MagicMock(return_value=freq_nominal)
        mock_ed.__getitem__ = MagicMock(return_value=mock_beam)

        mock_ecs_module = MagicMock()
        mock_ecs_module.conform_channel_order = mock_conform
        mock_ecs_module.ecs_ds2dict = mock_ds2dict

        with patch.object(ecs_mod, "parse_ecs", mock_parse), \
             patch.dict("sys.modules", {"echopype.calibrate.ecs": mock_ecs_module}), \
             patch("oceanstream.echodata.calibrate.saildrone.detect_pulse_mode", mock_detect):
            env, cal = ecs_mod.build_cal_params_from_ecs(
                mock_ed,
                ecs_short="short text",
                ecs_long="long text",
            )

        assert mock_parse.call_count == 2
        # Channel 0 = "short" → picks short_cal (25.0)
        # Channel 1 = "long"  → picks long_cal (30.0)
        assert cal["gain"][0] == 25.0
        assert cal["gain"][1] == 30.0

    def test_mode_count_mismatch(self):
        """Raise ValueError when detect_pulse_mode returns wrong count."""
        import xarray as xr

        import oceanstream.echodata.calibrate.ecs as ecs_mod

        mock_detect = MagicMock(return_value=["short"])  # 1 mode but 2 channels

        mock_ds = MagicMock()
        mock_parse = MagicMock(return_value={"env": mock_ds, "cal": mock_ds, "cal_BB": None})

        mock_ed = MagicMock()
        freq_nominal = xr.DataArray([38000.0, 200000.0], dims=["channel"])
        mock_beam = MagicMock()
        mock_beam.__getitem__ = MagicMock(return_value=freq_nominal)
        mock_ed.__getitem__ = MagicMock(return_value=mock_beam)

        mock_ecs_module = MagicMock()
        mock_ecs_module.conform_channel_order = MagicMock(side_effect=lambda ds, f: ds)
        mock_ecs_module.ecs_ds2dict = MagicMock(side_effect=lambda ds: {})

        with patch.object(ecs_mod, "parse_ecs", mock_parse), \
             patch.dict("sys.modules", {"echopype.calibrate.ecs": mock_ecs_module}), \
             patch("oceanstream.echodata.calibrate.saildrone.detect_pulse_mode", mock_detect):
            with pytest.raises(ValueError, match="detect_pulse_mode returned"):
                ecs_mod.build_cal_params_from_ecs(
                    mock_ed, ecs_short="s", ecs_long="l"
                )
