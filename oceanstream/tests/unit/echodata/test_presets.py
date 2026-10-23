"""Tests for the denoise preset library (oceanstream.echodata.presets)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from oceanstream import cli as cli_module
from oceanstream.echodata.config import EchodataConfig
from oceanstream.echodata.presets import (
    PresetError,
    get_preset,
    list_presets,
    load_preset,
    match_presets,
    parse_preset,
    uncovered_frequencies,
)
from oceanstream.echodata.presets.registry import diff
from oceanstream.echodata.presets.schema import FIELDS, to_pipeline_value

MASK_METHODS = ("impulse", "attenuation", "transient")
INERT_BACKGROUND = {f.name for f in FIELDS if f.group == "background" and not f.batch}

GOOD_FREQ = """
[echodata.denoise.frequency_params.38000]
impulse     = { vertical_bin_size = "5m", ping_lags = [1, 2], threshold_db = 12.0 }
attenuation = { upper_limit_sl = 400.0, lower_limit_sl = 500.0, num_side_pings = 100, threshold = 10.0 }
transient   = { exclude_above = 200.0, depth_bin = 20.0, n_pings = 50, thr_dB = 15.0, maxts = -35.0 }
background  = { range_window = 30, ping_window = 50, SNR_threshold = "5.0dB", background_noise_max = "-125.0dB" }
"""  # noqa: E501

HEADER = """
[preset]
key = "test-preset"
name = "Test"
status = "draft"
summary = "A test preset."

[echodata.denoise]
methods = ["impulse", "attenuation", "transient", "background"]
"""


class TestLibrary:
    def test_every_file_loads_cleanly(self):
        presets = list_presets()
        assert {p.key for p in presets} >= {"ryan-inspired", "tpos-sd-2023", "munkholmen-fjord"}
        for preset in presets:
            assert preset.source is not None and preset.source.stem == preset.key
            assert preset.notes == (), f"{preset.key}: {preset.notes}"

    @pytest.mark.parametrize("preset", list_presets(), ids=lambda p: p.key)
    def test_pipeline_loader_reads_exactly_the_preset(self, preset):
        """FREQUENCY_PRESETS is merged under a TOML; a preset must leave it nothing to add."""
        denoise = EchodataConfig.from_toml(preset.source).denoise
        expected = {str(f): groups for f, groups in preset.frequency_params().items()}
        for method in MASK_METHODS:
            assert denoise.to_frequency_keyed_params(method) == {
                f: groups[method] for f, groups in expected.items()
            }
        assert denoise.reference_ping_interval_s == preset.reference_ping_interval_s
        assert denoise.session_gap_minutes == preset.session_gap_minutes
        assert preset.to_denoise_config().session_gap_minutes == preset.session_gap_minutes
        background = denoise.to_frequency_keyed_params("background")
        for freq, params in background.items():
            for name, value in expected[freq]["background"].items():
                assert params[name] == value
            assert set(params) - set(expected[freq]["background"]) <= INERT_BACKGROUND

    def test_tpos_is_ryan_inspired_at_its_frequencies(self):
        changes = diff(get_preset("ryan-inspired"), get_preset("tpos-sd-2023"))
        assert changes == ["120 kHz: only in ryan-inspired", "333 kHz: only in ryan-inspired"]

    def test_statuses_agree_with_validations(self):
        for preset in list_presets():
            if preset.status == "production":
                assert preset.adopted_on, f"{preset.key} is production but adopted nowhere"
            if preset.status == "draft":
                assert not preset.adopted_on, f"{preset.key} is a draft but marked adopted"

    def test_unknown_key(self):
        with pytest.raises(PresetError, match="Known presets"):
            get_preset("no-such-preset")


class TestValidation:
    def test_minimal_preset(self):
        preset = parse_preset(HEADER + GOOD_FREQ)
        assert preset.frequencies == (38000,)
        impulse = preset.params[38000]["impulse"]
        assert impulse["vertical_bin_size"] == pytest.approx(5)
        assert preset.frequency_params()[38000]["impulse"]["vertical_bin_size"] == "5m"

    def test_missing_required_parameter(self):
        text = HEADER + GOOD_FREQ.replace(", maxts = -35.0", "")
        with pytest.raises(PresetError, match=r"transient\.maxts is missing"):
            parse_preset(text)

    def test_missing_filter_table(self):
        text = HEADER + "\n".join(
            line for line in GOOD_FREQ.splitlines() if not line.startswith("transient")
        )
        with pytest.raises(PresetError, match="no 'transient' table"):
            parse_preset(text)

    def test_unknown_parameter(self):
        text = HEADER + GOOD_FREQ.replace("threshold_db = 12.0", "threshold_db = 12.0, thresh = 1")
        with pytest.raises(PresetError, match=r"unknown parameter\(s\) \['thresh'\]"):
            parse_preset(text)

    def test_retired_parameter_dropped_with_note(self):
        text = HEADER + GOOD_FREQ.replace("threshold_db = 12.0", "threshold_db = 12.0, num_lags = 3")
        preset = parse_preset(text)
        assert "num_lags" not in preset.params[38000]["impulse"]
        assert any("num_lags" in note for note in preset.notes)

    def test_parameter_ignored_by_batch_pipeline_is_noted(self):
        text = HEADER + GOOD_FREQ.replace('"-125.0dB"', '"-125.0dB", depth_quantile = 0.2')
        preset = parse_preset(text)
        assert any("depth_quantile" in n and "batch pipeline" in n for n in preset.notes)

    def test_reference_band_order(self):
        text = HEADER + GOOD_FREQ.replace("lower_limit_sl = 500.0", "lower_limit_sl = 300.0")
        with pytest.raises(PresetError, match="upper_limit_sl < lower_limit_sl"):
            parse_preset(text)

    def test_out_of_bounds(self):
        text = HEADER + GOOD_FREQ.replace("n_pings = 50", "n_pings = 0")
        with pytest.raises(PresetError, match=r"n_pings must be >= 1"):
            parse_preset(text)

    def test_unknown_preset_metadata_key(self):
        text = HEADER.replace('summary = "A test preset."', 'summary = "x"\ncolour = "red"')
        with pytest.raises(PresetError, match="colour"):
            parse_preset(text + GOOD_FREQ)

    def test_unknown_platform(self):
        text = HEADER + '\n[preset.applies_to]\nplatforms = ["submarine"]\n' + GOOD_FREQ
        with pytest.raises(PresetError, match="submarine"):
            parse_preset(text)

    def test_key_must_match_file_name(self, tmp_path: Path):
        path = tmp_path / "other-name.toml"
        path.write_text(HEADER + GOOD_FREQ)
        with pytest.raises(PresetError, match="does not match the file name"):
            load_preset(path)

    def test_plain_denoise_config_loads_as_external(self, tmp_path: Path):
        path = tmp_path / "lab-export.toml"
        path.write_text('[echodata.denoise]\nmethods = ["impulse", "attenuation", '
                        '"transient", "background"]\n' + GOOD_FREQ)
        preset = load_preset(path)
        assert (preset.key, preset.status) == ("lab-export", "external")

    def test_timing_settings(self):
        timing = "reference_ping_interval_s = 2.8\nsession_gap_minutes = 10\n"
        text = HEADER.replace("methods = [", timing + "methods = [")
        preset = parse_preset(text + GOOD_FREQ)
        assert (preset.reference_ping_interval_s, preset.session_gap_minutes) == (2.8, 10.0)
        assert preset.params_sha256 != parse_preset(HEADER + GOOD_FREQ).params_sha256

    @pytest.mark.parametrize("bad", ["session_gap_minutes = 0", "reference_ping_interval_s = -1",
                                     "session_gap = 10"])
    def test_bad_timing_settings(self, bad):
        text = HEADER.replace("methods = [", f"{bad}\nmethods = [")
        with pytest.raises(PresetError):
            parse_preset(text + GOOD_FREQ)

    def test_ryan_detector_keeps_its_percentile(self):
        text = HEADER + GOOD_FREQ.replace("maxts = -35.0", 'maxts = -35.0, detector = "ryan", percentile = 15')
        transient = parse_preset(text).params[38000]["transient"]
        assert (transient["detector"], transient["percentile"]) == ("ryan", 15.0)

    def test_percentile_without_ryan_is_dropped(self):
        preset = parse_preset(HEADER + GOOD_FREQ.replace("maxts = -35.0", "maxts = -35.0, percentile = 15"))
        assert "percentile" not in preset.params[38000]["transient"]
        assert any("ryan detector" in n for n in preset.notes)

    def test_unknown_detector(self):
        with pytest.raises(PresetError, match="detector"):
            parse_preset(HEADER + GOOD_FREQ.replace("maxts = -35.0", 'maxts = -35.0, detector = "x"'))

    def test_params_hash_ignores_metadata(self):
        a = parse_preset(HEADER + GOOD_FREQ)
        b = parse_preset(HEADER.replace("A test preset.", "Reworded.") + GOOD_FREQ)
        c = parse_preset(HEADER + GOOD_FREQ.replace("threshold_db = 12.0", "threshold_db = 11.0"))
        assert a.params_sha256 == b.params_sha256 != c.params_sha256

    @pytest.mark.parametrize(
        ("group", "name", "value", "expected"),
        [
            ("impulse", "vertical_bin_size", 2.0, "2m"),
            ("impulse", "vertical_bin_size", 2.5, "2.5m"),
            ("background", "SNR_threshold", 5.25, "5.25dB"),
            ("background", "background_noise_max", -125.0, "-125.0dB"),
            ("impulse", "threshold_db", 12.0, 12.0),
        ],
    )
    def test_pipeline_values(self, group, name, value, expected):
        assert to_pipeline_value(group, name, value) == expected


class TestCoverage:
    def test_uncovered_frequencies(self):
        params = {38000: {}, "200000": {}}
        assert uncovered_frequencies(params, [38000.0, 200000.0]) == []
        assert uncovered_frequencies(params, [18000.0, 38000.0, 70000.0]) == [18000, 70000]

    def test_preset_coverage(self):
        assert get_preset("tpos-sd-2023").uncovered([18e3, 38e3, 200e3]) == [18000]


class TestMatch:
    def test_ship_survey_prefers_the_five_frequency_preset(self):
        matches = match_presets([18, 38, 70, 120, 200], platform="research-vessel",
                                regime="open-ocean", water_depth_m=(3000, 5500))
        family = {m.preset.key for m in matches if not m.missing_khz}
        # The rejected -scaled, -sensitive and -perping trials are deprecated and never match.
        open_ocean = {"research-vessel-open-ocean", "research-vessel-open-ocean-ryan-tn",
                      "research-vessel-open-ocean-ryan-short",
                      "research-vessel-open-ocean-ryan-margin"}
        assert family == open_ocean | {"research-vessel-nordic-shelf"}
        # The shelf preset covers the frequencies but ranks below the open-ocean ones.
        assert {m.preset.key for m in matches[:4]} == open_ocean

    def test_saildrone_survey_prefers_the_adopted_preset(self):
        matches = match_presets([38, 200], platform="usv", regime="open-ocean",
                                water_depth_m=(4000, 5000))
        assert [m.preset.key for m in matches[:2]] == ["tpos-sd-2023", "tropical-pacific-v3"]

    def test_drafts_can_be_left_out(self):
        keys = {m.preset.key for m in match_presets([38], include_drafts=False)}
        assert "research-vessel-open-ocean" not in keys

    def test_bad_platform(self):
        with pytest.raises(PresetError, match="platform"):
            match_presets([38], platform="submarine")


class TestCli:
    runner = CliRunner()

    def _run(self, *args: str):
        return self.runner.invoke(cli_module.app, ["process", "echodata", "presets", *args],
                                  env={"OCEANSTREAM_NO_MCP_PROMPT": "1"})

    def test_list(self):
        result = self._run("list")
        assert result.exit_code == 0, result.output
        assert "tpos-sd-2023  [production]" in result.output

    def test_show(self):
        result = self._run("show", "munkholmen-fjord")
        assert result.exit_code == 0, result.output
        assert "Changes against ryan-inspired" in result.output
        assert "MUNKHOLMEN_2026" in result.output

    def test_match(self):
        result = self._run("match", "-f", "18,38,70,120,200", "--platform", "research-vessel",
                           "--regime", "open-ocean")
        assert result.exit_code == 0, result.output
        assert result.output.splitlines()[0].endswith("research-vessel-open-ocean [draft]")

    def test_check_coverage(self):
        assert self._run("check", "tpos-sd-2023", "-f", "38,200").exit_code == 0
        result = self._run("check", "tpos-sd-2023", "-f", "18,38")
        assert result.exit_code == 1
        assert "18 kHz" in result.output

    def test_path(self):
        result = self._run("path", "tpos-sd-2023")
        assert result.exit_code == 0
        assert result.output.strip().endswith("library/tpos-sd-2023.toml")
