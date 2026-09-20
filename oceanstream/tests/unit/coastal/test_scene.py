"""Scene — one acquisition: validation, band lookup, solar-geometry resolution."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rasterio")

from oceanstream.coastal.io.rasters import RasterGrid, write_cog  # noqa: E402
from oceanstream.coastal.scene import (  # noqa: E402
    Scene,
    _date_from_filenames,
    _resolve_solar_zenith,
)
from oceanstream.coastal.sensors import PLEIADES_NEO, SENTINEL2  # noqa: E402

DEG_10M = 10.0 / 111_320.0
WAVELENGTHS = [444.0, 489.0, 561.0, 667.0, 835.0, 1612.0, 2191.0]


def grid(width: int = 5, height: int = 4) -> RasterGrid:
    return RasterGrid(width, height, (DEG_10M, 0.0, -9.212, 0.0, -DEG_10M, 38.420), "EPSG:4326")


def scene(**overrides: object) -> Scene:
    g = grid()
    kwargs: dict[str, object] = {
        "wavelengths_nm": WAVELENGTHS,
        "rrs_above": np.full((len(WAVELENGTHS), *g.shape), 0.01, np.float32),
        "grid": g,
        "solar_zenith_deg": 22.4,
        "acquisition_date": dt.date(2026, 6, 27),
    }
    kwargs.update(overrides)
    return Scene.from_arrays(**kwargs)  # type: ignore[arg-type]


class TestConstruction:
    def test_from_arrays(self) -> None:
        s = scene()
        assert s.n_bands == len(WAVELENGTHS)
        assert s.shape == (4, 5)
        assert s.sensor is SENTINEL2

    def test_sensor_can_be_named_by_alias(self) -> None:
        assert scene(sensor="S2").sensor is SENTINEL2

    def test_sensor_can_be_passed_as_a_profile(self) -> None:
        g = grid()
        s = Scene.from_arrays(
            wavelengths_nm=list(PLEIADES_NEO.bands_nm),
            rrs_above=np.zeros((6, *g.shape), np.float32),
            grid=g,
            solar_zenith_deg=30.0,
            sensor=PLEIADES_NEO,
        )
        assert s.sensor is PLEIADES_NEO

    def test_band_count_must_match_wavelength_count(self) -> None:
        g = grid()
        with pytest.raises(ValueError, match="wavelengths but .* bands"):
            Scene.from_arrays(
                wavelengths_nm=WAVELENGTHS,
                rrs_above=np.zeros((3, *g.shape), np.float32),
                grid=g,
                solar_zenith_deg=22.4,
            )

    def test_rasters_must_match_the_grid(self) -> None:
        # A silent shape disagreement here would misregister every band ratio.
        with pytest.raises(ValueError, match="does not match its grid"):
            Scene.from_arrays(
                wavelengths_nm=WAVELENGTHS,
                rrs_above=np.zeros((len(WAVELENGTHS), 9, 9), np.float32),
                grid=grid(),
                solar_zenith_deg=22.4,
            )

    def test_2d_input_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"must be \(n_bands, H, W\)"):
            Scene.from_arrays(
                wavelengths_nm=[561.0],
                rrs_above=np.zeros((4, 5), np.float32),
                grid=grid(),
                solar_zenith_deg=22.4,
            )

    @pytest.mark.parametrize("sza", [-1.0, 90.0, 120.0])
    def test_impossible_solar_zenith_rejected(self, sza: float) -> None:
        # A zenith of 90 degrees or more means the sun is below the horizon;
        # the air-water transmission terms go singular rather than merely wrong.
        with pytest.raises(ValueError, match=r"must be in \[0, 90\)"):
            scene(solar_zenith_deg=sza)

    def test_rhos_must_match_rrs_shape(self) -> None:
        g = grid()
        with pytest.raises(ValueError, match="same shape"):
            Scene.from_arrays(
                wavelengths_nm=WAVELENGTHS,
                rrs_above=np.zeros((len(WAVELENGTHS), *g.shape), np.float32),
                grid=g,
                solar_zenith_deg=22.4,
                rhos=np.zeros((3, *g.shape), np.float32),
            )


class TestBandAccess:
    def test_band_by_wavelength(self) -> None:
        s = scene()
        s.rrs_above[2] = 0.05
        np.testing.assert_allclose(s.band(561.0), 0.05)

    def test_band_tolerates_platform_drift(self) -> None:
        assert scene().band(2202.0).shape == (4, 5)

    def test_absent_band_names_what_the_scene_carries(self) -> None:
        with pytest.raises(KeyError) as exc:
            scene().band(1000.0)
        assert "444" in str(exc.value)

    def test_rhos_band_without_rhos_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="carries no rhos"):
            scene().rhos_band(561.0)

    def test_rhos_band(self) -> None:
        g = grid()
        rhos = np.full((len(WAVELENGTHS), *g.shape), 0.03, np.float32)
        np.testing.assert_allclose(scene(rhos=rhos).rhos_band(561.0), 0.03)


class TestValidate:
    def test_a_full_sentinel2_scene_is_clean(self) -> None:
        assert scene().validate() == []

    def test_a_scene_missing_swir_is_flagged_but_still_usable(self) -> None:
        # Processable with a caveat, not fatal: the deglint and deep-water
        # screen degrade, the inversion itself does not.
        s = scene(
            wavelengths_nm=[444.0, 489.0, 561.0, 667.0, 835.0],
            rrs_above=np.zeros((5, 4, 5), np.float32),
        )
        assert any("deep-water screen" in p for p in s.validate())

    def test_describe_names_the_sensor_and_date(self) -> None:
        summary = scene().describe()
        assert summary["sensor"] == "sentinel2"
        assert summary["acquisition_date"] == "2026-06-27"


class TestSolarZenithResolution:
    def test_reads_the_acolite_settings_file(self, tmp_path: Path) -> None:
        (tmp_path / "acolite_run_settings.txt").write_text("sun_zenith=23.75\nlimit=1,2,3,4\n")
        value, source = _resolve_solar_zenith(tmp_path, None)
        assert value == pytest.approx(23.75)
        assert source == "settings"

    def test_accepts_the_sza_spelling(self, tmp_path: Path) -> None:
        (tmp_path / "settings.txt").write_text("sza = 31.0\n")
        assert _resolve_solar_zenith(tmp_path, None)[0] == pytest.approx(31.0)

    def test_falls_back_to_the_sza_raster(self, tmp_path: Path) -> None:
        g = grid()
        write_cog(tmp_path / "S2A_2026_06_27_L2R_sza.tif", np.full(g.shape, 25.0, np.float32), g)
        value, source = _resolve_solar_zenith(tmp_path, None)
        assert value == pytest.approx(25.0)
        assert source == "sza_raster"

    def test_sza_raster_ignores_out_of_range_fill(self, tmp_path: Path) -> None:
        # A zero-filled border would drag the scene mean toward nadir and
        # silently inflate every subsurface reflectance.
        g = grid()
        sza = np.full(g.shape, 25.0, np.float32)
        sza[0, :] = 0.0
        write_cog(tmp_path / "x_L2R_sza.tif", sza, g)
        assert _resolve_solar_zenith(tmp_path, None)[0] == pytest.approx(25.0)

    def test_settings_file_wins_over_the_raster(self, tmp_path: Path) -> None:
        g = grid()
        write_cog(tmp_path / "x_L2R_sza.tif", np.full(g.shape, 25.0, np.float32), g)
        (tmp_path / "settings.txt").write_text("sun_zenith=23.75\n")
        assert _resolve_solar_zenith(tmp_path, None)[1] == "settings"

    def test_explicit_fallback_is_last(self, tmp_path: Path) -> None:
        value, source = _resolve_solar_zenith(tmp_path, 22.4)
        assert value == pytest.approx(22.4)
        assert source == "fallback"

    def test_no_source_at_all_refuses_to_guess(self, tmp_path: Path) -> None:
        # Assuming a nadir sun would bias the whole retrieval rather than fail.
        with pytest.raises(ValueError, match="solar_zenith_fallback_deg"):
            _resolve_solar_zenith(tmp_path, None)


class TestDateFromFilenames:
    def test_underscore_form(self) -> None:
        assert _date_from_filenames([Path("S2A_MSI_2026_06_27_L2R_rhos_561.tif")]) == dt.date(
            2026, 6, 27
        )

    def test_hyphen_form(self) -> None:
        assert _date_from_filenames([Path("scene-2026-07-08_L2R_rhos_561.tif")]) == dt.date(
            2026, 7, 8
        )

    def test_no_date_returns_none(self) -> None:
        assert _date_from_filenames([Path("rhos_561.tif")]) is None

    def test_impossible_date_returns_none(self) -> None:
        assert _date_from_filenames([Path("x_2026_13_45_L2R_rhos_561.tif")]) is None


class TestFromAcoliteDir:
    def _write(self, directory: Path, wavelengths: list[int], level: str = "L2R_rhos") -> None:
        directory.mkdir(parents=True, exist_ok=True)
        g = grid()
        for i, nm in enumerate(wavelengths):
            write_cog(
                directory / f"S2A_MSI_2026_06_27_{level}_{nm}.tif",
                np.full(g.shape, 0.01 * (i + 1), np.float32),
                g,
            )

    def test_loads_bands_in_ascending_wavelength_order(self, tmp_path: Path) -> None:
        # Directory listing order is not wavelength order, and every downstream
        # index assumes it is.
        self._write(tmp_path, [1612, 444, 561, 667, 489, 835])
        s = Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)
        assert list(s.wavelengths_nm) == [444.0, 489.0, 561.0, 667.0, 835.0, 1612.0]

    def test_infers_the_date_from_the_filenames(self, tmp_path: Path) -> None:
        self._write(tmp_path, [444, 561, 667])
        s = Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)
        assert s.acquisition_date == dt.date(2026, 6, 27)

    def test_explicit_date_overrides_the_filename(self, tmp_path: Path) -> None:
        self._write(tmp_path, [444, 561, 667])
        s = Scene.from_acolite_dir(
            tmp_path, acquisition_date=dt.date(2020, 1, 1), solar_zenith_fallback_deg=22.4
        )
        assert s.acquisition_date == dt.date(2020, 1, 1)

    def test_l2r_gives_rhos_and_a_derived_rrs(self, tmp_path: Path) -> None:
        # rhos/pi is the standard above-water conversion; flagging it matters
        # because it is not the same quantity ACOLITE's own L2W Rrs reports.
        self._write(tmp_path, [444, 561, 667])
        s = Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)
        assert s.rrs_derived_from_rhos
        assert s.rhos is not None
        np.testing.assert_allclose(s.rrs_above, s.rhos / np.pi, rtol=1e-6)

    def test_l2w_rrs_is_used_directly(self, tmp_path: Path) -> None:
        self._write(tmp_path, [444, 561, 667], level="L2W_Rrs")
        s = Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)
        assert not s.rrs_derived_from_rrs if hasattr(s, "rrs_derived_from_rrs") else True
        assert not s.rrs_derived_from_rhos

    def test_records_the_source_directory(self, tmp_path: Path) -> None:
        self._write(tmp_path, [444, 561, 667])
        s = Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)
        assert s.source_dir == tmp_path

    def test_empty_directory_names_what_it_looked_for(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="rhos"):
            Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)

    def test_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            Scene.from_acolite_dir(tmp_path / "nope", solar_zenith_fallback_deg=22.4)

    def test_mismatched_band_grids_are_refused(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        a, b = grid(), grid(width=9)
        write_cog(tmp_path / "S2A_2026_06_27_L2R_rhos_444.tif", np.zeros(a.shape, np.float32), a)
        write_cog(tmp_path / "S2A_2026_06_27_L2R_rhos_561.tif", np.zeros(b.shape, np.float32), b)
        with pytest.raises(ValueError, match="grid"):
            Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)

    def test_rgb_composite_is_not_loaded_as_a_band(self, tmp_path: Path) -> None:
        self._write(tmp_path, [444, 561, 667])
        (tmp_path / "S2A_MSI_2026_06_27_L2R_rgb_rhos.tif").touch()
        s = Scene.from_acolite_dir(tmp_path, solar_zenith_fallback_deg=22.4)
        assert s.n_bands == 3


class TestAcoliteTime:
    """ACOLITE writes UTC, but only some sensors carry the offset."""

    def test_offset_form_is_preserved(self) -> None:
        from oceanstream.coastal.scene import _acolite_time

        assert _acolite_time("2026-09-03T11:40:40.393642+00:00") == dt.datetime(
            2026, 9, 3, 11, 40, 40, 393642, tzinfo=dt.UTC
        )

    def test_naive_pleiades_neo_form_is_read_as_utc(self) -> None:
        # ACOLITE's PNeo output omits the offset; rejecting it made every PNeo
        # scene unloadable.
        from oceanstream.coastal.scene import _acolite_time

        assert _acolite_time("2026-09-03T11:33:59.900000") == dt.datetime(
            2026, 9, 3, 11, 33, 59, 900000, tzinfo=dt.UTC
        )
