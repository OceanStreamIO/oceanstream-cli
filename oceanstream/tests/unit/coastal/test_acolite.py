"""ACOLITE driver — settings assembly, output completeness, filename parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from oceanstream.coastal.acolite import (
    acolite_settings,
    correct_scene,
    missing_windows,
    output_is_complete,
    run_acolite,
    wavelength_of,
    write_acolite_settings,
)

SESIMBRA_LIMIT = (38.150, -9.300, 38.550, -8.800)


def touch_l2r(directory: Path, wavelengths: list[int], prefix: str = "S2A_MSI_2026_06_27") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for nm in wavelengths:
        (directory / f"{prefix}_L2R_rhos_{nm}.tif").touch()


class TestWavelengthOf:
    def test_parses_the_trailing_token(self) -> None:
        assert wavelength_of(Path("S2A_MSI_2026_06_27_L2R_rhos_1612.tif")) == 1612.0

    def test_accepts_a_string(self) -> None:
        assert wavelength_of("x_L2W_Rrs_444.tif") == 444.0

    def test_accepts_a_fractional_wavelength(self) -> None:
        assert wavelength_of("x_L2R_rhos_664.6.tif") == pytest.approx(664.6)

    def test_non_numeric_tail_is_an_actionable_error(self) -> None:
        with pytest.raises(ValueError, match="is not numeric"):
            wavelength_of(Path("S2A_MSI_2026_06_27_L2R_rgb_rhos.tif"))


class TestSettings:
    def test_contains_the_three_keys_acolite_cannot_run_without(self) -> None:
        body = acolite_settings(SESIMBRA_LIMIT, Path("/in/scene.SAFE"), Path("/out"))
        assert "inputfile=/in/scene.SAFE" in body
        assert "output=/out" in body
        assert "limit=38.15,-9.3,38.55,-8.8" in body

    def test_limit_keeps_acolite_ordering(self) -> None:
        # AOI.acolite_limit already reorders to (south, west, north, east); this
        # must pass it through untouched or the subset lands in the wrong place.
        body = acolite_settings(SESIMBRA_LIMIT, Path("in"), Path("out"))
        limit = next(line for line in body.splitlines() if line.startswith("limit="))
        assert limit == "limit=38.15,-9.3,38.55,-8.8"

    def test_template_is_appended_not_parsed(self, tmp_path: Path) -> None:
        template = tmp_path / "t.txt"
        template.write_text("l2w_parameters=Rrs_*\nglint_correction=True\n")
        body = acolite_settings(SESIMBRA_LIMIT, Path("in"), Path("out"), template=template)
        assert "l2w_parameters=Rrs_*" in body
        assert "glint_correction=True" in body

    def test_injected_keys_come_last_so_they_win(self, tmp_path: Path) -> None:
        # ACOLITE takes the last assignment; a template that also sets output
        # must not be able to redirect the run.
        template = tmp_path / "t.txt"
        template.write_text("output=/somewhere/else\n")
        body = acolite_settings(SESIMBRA_LIMIT, Path("in"), Path("/out"), template=template)
        assert body.rindex("output=/out") > body.index("output=/somewhere/else")

    def test_extra_overrides_are_injected(self) -> None:
        body = acolite_settings(
            SESIMBRA_LIMIT, Path("in"), Path("out"), extra={"dsf_aot_estimate": "fixed"}
        )
        assert "dsf_aot_estimate=fixed" in body

    def test_missing_template_names_the_path(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="settings template not found"):
            acolite_settings(
                SESIMBRA_LIMIT, Path("in"), Path("out"), template=tmp_path / "nope.txt"
            )

    def test_write_lands_in_the_output_directory(self, tmp_path: Path) -> None:
        path = write_acolite_settings(tmp_path / "out", SESIMBRA_LIMIT, Path("in"))
        assert path == tmp_path / "out" / "acolite_settings.txt"
        assert "inputfile=" in path.read_text()


class TestOutputCompleteness:
    def test_complete_l2r_output(self, tmp_path: Path) -> None:
        touch_l2r(tmp_path, [444, 489, 561, 667, 835, 1612, 2191])
        assert output_is_complete(tmp_path)
        assert missing_windows(tmp_path) == []

    def test_l2w_rrs_also_counts(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        for nm in (443, 560, 665):
            (tmp_path / f"S2A_L2W_Rrs_{nm}.tif").touch()
        assert output_is_complete(tmp_path)

    def test_a_dropped_green_band_is_detected(self, tmp_path: Path) -> None:
        # ACOLITE can exit 0 having written a partial set; without this check
        # the retrieval runs on whatever bands happened to survive.
        touch_l2r(tmp_path, [444, 489, 667, 835])
        assert not output_is_complete(tmp_path)
        assert missing_windows(tmp_path) == ["green"]

    def test_reports_every_missing_window(self, tmp_path: Path) -> None:
        touch_l2r(tmp_path, [835, 1612])
        assert missing_windows(tmp_path) == ["blue", "green", "red"]

    def test_rgb_composite_is_not_mistaken_for_a_band(self, tmp_path: Path) -> None:
        # The digit class in the glob exists precisely to exclude this file.
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "S2A_MSI_2026_06_27_L2R_rgb_rhos.tif").touch()
        assert not output_is_complete(tmp_path)

    def test_absent_directory_is_incomplete_not_an_error(self, tmp_path: Path) -> None:
        assert not output_is_complete(tmp_path / "never_ran")

    def test_empty_directory_is_incomplete(self, tmp_path: Path) -> None:
        assert not output_is_complete(tmp_path)


class TestRunner:
    def test_missing_acolite_names_the_flag_and_the_source(self, tmp_path: Path) -> None:
        settings = tmp_path / "s.txt"
        settings.write_text("inputfile=x\n")
        with pytest.raises(FileNotFoundError) as exc:
            run_acolite(settings, tmp_path / "no_acolite")
        message = str(exc.value)
        assert "--acolite-path" in message
        assert "ACOLITE_PATH" in message
        assert "github.com/acolite/acolite" in message

    def test_missing_settings_file_is_caught_before_launching(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="settings file not found"):
            run_acolite(tmp_path / "nope.txt", tmp_path)

    def test_complete_output_skips_the_subprocess(self, tmp_path: Path) -> None:
        # ACOLITE runs take minutes; re-running a finished scene on every flow
        # retry is the difference between a usable pipeline and an unusable one.
        out = tmp_path / "out"
        touch_l2r(out, [444, 489, 561, 667, 835])
        result = correct_scene(
            input_path=tmp_path / "scene.SAFE",
            output_dir=out,
            aoi_limit=SESIMBRA_LIMIT,
            acolite_path=tmp_path / "definitely_not_acolite",
        )
        assert result == out

    def test_force_ignores_existing_output(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        touch_l2r(out, [444, 489, 561, 667, 835])
        with pytest.raises(FileNotFoundError):
            correct_scene(
                input_path=tmp_path / "scene.SAFE",
                output_dir=out,
                aoi_limit=SESIMBRA_LIMIT,
                acolite_path=tmp_path / "definitely_not_acolite",
                force=True,
            )
