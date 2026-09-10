# Plan: `oceanstream.coastal` — coastal optics library

Port the `lee_demo` prototype (57 files, untracked, single-site) into a generic
`oceanstream.coastal` module in `sd-data-ingest`, close the physics gaps, and
validate at 3–4 contrasting AOIs. Standalone library only — EDITO service is a
follow-on.

## Naming (decided 2026-09-09)

`oceanstream.coastal`, NOT `oceanstream.benthic`.

- "Benthic" overpromises depth (module is capped at z_max ≈ 12–16 m) AND
  underpromises scope: 4 of the 7 planned products (z_max, k(λ), seabed PAR, SDB)
  are water-column or geometric, not seafloor. "Benthic" names the minority.
- `kelp_observe/docs/pipeline-architecture.md` §3 had ALREADY retired the name
  `oceanstream.benthic` — see
  `kelpobserve-app/.github/prompts/plan-leeInversionHabitatMapDemo.prompt.md:29`.
  This ratifies that decision.
- `oceanstream.coastal` has ZERO existing references anywhere in the workspace.
- Leaves room for future non-optics coastal work (wave exposure, shoreline change)
  without a second rename; optics stays a subpackage.
- Extra: `pip install "oceanstream[coastal]"`. CLI: `oceanstream process coastal …`

**Rename scope — do NOT bulk find/replace.** ~191 matches for "benthic" across the
workspace; the large majority are legitimate science vocabulary ("benthic habitat",
"effective benthic reflectance", "benthic monitoring") that must stay. Only the
*module/extra/CLI identifiers* change. Files carrying the module name:

- `kelp_observe/.github/copilot-instructions.md`
- `os-webapp/.github/copilot-instructions.md` (flow table + `oceanstream[benthic]`)
- `kelp_observe/docs/lee_inversion/conclusion.md`

Historical prompt/review files record that it never existed — leave as-is.

## Status (2026-09-10)

Session 1 of the port. Phases 0 and 1.0–1.1 are landed; check-in gate held
before continuing.

**Landed.**

- Phase 0 fully done (see §0.1–0.4 ticks below). Phase 0.1 changes are
  *staged*, not committed — the user will commit at the end of the session.
- Phase 1.0: skeleton at `sd-data-ingest/oceanstream/coastal/` with 23 files;
  `coastal` + `coastal-acquire` extras added to `pyproject.toml`;
  `ProcessingModule` Literal in `providers/base.py` extended with `"coastal"`;
  CLI stub `oceanstream process coastal {detect,attenuation,detectability,aoi,qc}`
  registered — each subcommand raises `NotImplementedError` with a phase pointer.
- Phase 1.1: `water.py`, `lee.py` ported; `SceneIOPs` dataclass + internal
  helpers (`_u_from_rrs`, `_kd_lee_2005`) landed in
  `coastal/optics/qaa.py`; `fit_scene_iops` / `kd_map` stubbed for Phase 1.2.
  `invert_rho_b` was renamed to `invert_scene` and `forward_model` promoted
  to public API; the old name is kept as an alias for the prototype.
- Reference test fixture bundled at
  `oceanstream/tests/data/coastal/scene_iops_sesimbra_2026_06_27.json`
  (11-band QAA scene fit; original `NaN` normalised to `null`).
- Placeholder modules created for every not-yet-ported symbol
  (`masks.py`, `classify.py`, `bathymetry/{stumpf,terrain}.py`,
  `optics/attenuation.py`, `qc/{ac_uncertainty,point_diagnostic,floors}.py`,
  `aoi.py`, `sensors.py`, `scene.py`, `processor.py`). Each stub raises
  `NotImplementedError` referencing the phase that will fill it. This keeps
  the lazy `__init__.py` mypy-clean during the phased port.

**Verification state.**

- `oceanstream/tests/unit/coastal/test_lee_inversion.py`: **19 passed +
  1 xpass** (byte-identical to the prototype's 20/20). The prototype's
  `sigma_rho_b` xfail note was stale — the shipped derivative matches
  finite-difference to within 5%; the port carries an `xfail(strict=False)`
  as a regression trap in case a future change re-introduces drift.
- `mypy oceanstream/coastal`: **clean on 23 files**.
- Ruff: 38 warnings, all in the same rule buckets that echodata's lazy
  `__getattr__` already fires (`PLR0911/0912`, `PLC0415`, `PLR2004` on
  physics tolerances). Auto-fixed `I001` and `F401`. Not treated as
  blocking, consistent with the repo baseline (3,492 warnings tree-wide).
- Narrow regression scope (coastal + providers + cli tests): 55 passed +
  1 xpass. Wider `pytest -m "not integration"` cannot run cleanly today
  because of two pre-existing collection errors (see Open).

**Golden regression (Verification §2).**

Wired in `oceanstream/tests/unit/coastal/test_golden_sesimbra.py`. The ported
fitter reproduces the prototype's recorded run to ~1e-8 — `k(444) = 0.108475`,
intercept, R², `n_bins = 19` and `n_pixels = 4440` all match. Fixture is the
real Sesimbra 2026-06-27 ACOLITE scene clipped to the habitat AOI (222 × 175,
6167 water pixels, 510 KB float32 npz), extracted with the prototype's own
`load_scene` so the inputs are provably identical. Per-band deep-water
references are hard-coded because the prototype draws them from a deepwater
AOI *outside* the habitat clip on the full 4441 × 3605 scene.

**Next slice** is the Phase 3 check-in gate.

## Session 2 (2026-09-10) — Phases 1.2 → 1.6 + golden regression

**Landed.**

- **Phase 1.2**: full `qaa.py` port. `RRS_670_TURBID_THRESHOLD` and
  `A_CDM_443_MIN` extracted into `RetrievalConfig`. The prototype's
  `SiteProfile.a_cdm_443_min` was dead code, so the port implements a
  two-tier scheme instead: `a_cdm_443_max_fatal` (−0.03) reproduces the
  prototype's flag verbatim for regression safety, and `a_cdm_443_min`
  (−0.005) adds a softer `a_cdm_443_below_aoi_bound` flag. The Phase-0.2
  `kd_map` NameError fix carried across.
- **Phase 1.3**: `masks.py`, `classify.py`, `bathymetry/stumpf.py` ported.
  `MaskConfig` rewritten and `BathymetryConfig` added. Cluster labels are
  `spectral_class_N`, never habitat names. Stumpf accuracy is reported as
  absolute error per depth stratum, never as R².
- **Phase 1.4**: `terrain_classes` promoted to `bathymetry/terrain.py` with
  five thresholds added to `BathymetryConfig`. `flat` and `rugose`
  deliberately do not partition the scene — an ambiguous pixel belongs to
  neither rather than being forced into a class to improve coverage.
- **Phase 1.5**: `optics/attenuation.py` — `BandCalibration`,
  `deep_water_reference`, `fit_band_attenuation`, `calibrate_bands`,
  `lyzenga_ratios`. `AttenuationConfig` was rewritten: its Phase-1.0
  placeholder values did **not** match the prototype and would have made the
  golden regression meaningless. Defaults now reproduce the prototype
  exactly, and a `min_bins` guard was added.
- **Phase 1.6**: `qc/ac_uncertainty.py` and `qc/point_diagnostic.py` ported,
  with a new `QCConfig`. Both were generalised off the prototype's hard-coded
  10 m Sentinel-2 grid: block sizes are configured in metres and converted
  using a `pixel_size_m` argument. `qc/floors.py` remains stubbed for
  Phase 3.3.

**Two genuine defects found and fixed during the port.**

Both were latent in the prototype and both fail silently, which is why they
are recorded here rather than in a commit message.

1. `adjacency_buffer(land, buffer_px=0)` masked the entire scene.
   `scipy.ndimage.binary_dilation` reads `iterations=0` as "dilate until
   nothing changes", so a perfectly reasonable "no buffer" configuration
   would have flooded the scene with land and masked everything. Fixed with
   an explicit short-circuit.
2. `terrain_classes` classified NaN-depth pixels as `flat`. `np.gradient`'s
   central difference never reads the centre pixel, and scipy's min/max
   filters do not propagate NaN reliably, so a lone data hole in otherwise
   flat bathymetry came back with slope 0 and relief 0 — and became a
   positive control for "sand". Fixed by adding `np.isfinite(depth_m)` to
   the usable mask; the test doubles as a regression trap.

**Verification state.**

- `pytest oceanstream/tests/unit/coastal`: **166 passed + 1 xpass** across
  nine modules (lee, qaa, masks, stumpf, classify, terrain, attenuation, qc,
  golden).
- `mypy oceanstream/coastal`: **clean on 23 files**.
- Ruff on `oceanstream/coastal`: 41 warnings, all in the accepted baseline
  buckets (`PLC0415` from the lazy `__getattr__`, `PLR0913` on band-passing
  signatures, `PLR2004` on physics tolerances).
- Scope pytest to `oceanstream/tests/unit/coastal`; the repo-wide unit run
  still cannot complete because of the two pre-existing collection errors
  noted under Open.

**Note for Phase 5.** The golden regression asserts *faithful reproduction of
the prototype*, not correctness. The pinned k values are a known-biased
calibration and the test must be re-baselined once Phase 5 resolves the
additive offset. The test docstring says so explicitly.

## Session 3 (2026-09-10) — Phase 2, generic AOI + sensor + adapters

**Landed.** Phases 2.1–2.6, all six modules exported from the lazy
`__init__.py`.

- **2.1 `aoi.py`**: `AOI` and `BathymetryReference`, both frozen. The
  prototype's single hard-coded site becomes `processing_bbox` (required) plus
  an optional `habitat_bbox`, with the containment checked at construction —
  a habitat box that escapes its processing grid is a configuration error that
  otherwise surfaces as an empty scene halfway through a run. `utm_epsg`
  derives Sesimbra's 32629 rather than carrying it as a constant.
  `depth_validation_is_primary` is True only when the *finest available*
  bathymetry sits on stable rock, so an AOI over mobile sediment cannot
  quietly claim a depth-validated result.
- **2.2 `sensors.py`**: `SensorProfile`, `SENTINEL2`, `PLEIADES_NEO`. Band
  roles are resolved by wavelength within a declared tolerance instead of by
  index, because the two sensors do not share a band order. The PNeo profile
  carries `deglint_reference_is_assumed_dark=False` and a note explaining
  that its 825 nm reference over-subtracts — the S2 SWIR assumption does not
  transfer, and encoding that as a flag rather than a comment is what stops
  it being applied by default.
- **2.3 `io/rasters.py`**: `RasterGrid` (hashable, JSON-safe), `read_band`,
  `read_stack`, `reproject_to_grid`, `write_cog`. `read_stack` refuses to
  stack rasters on differing grids; `pixel_size_m` cosine-corrects longitude
  so a geographic-CRS scene reports ~8.9 m rather than 10 m at 38.4°N.
  `write_cog` is atomic via `.tmp` + `os.replace`.
- **2.4 `bathymetry/tide.py`**: `TideCorrection`, the `TideProvider`
  protocol, `ConstantTide`, `NullTide`, `correct_depth`,
  `fit_offset_from_reference`. A correction records how it was derived, and
  `correct_depth(..., require_independent=True)` refuses a `fitted` offset:
  an offset regressed against the reference depths and then validated against
  those same depths is circular, and the refusal is the only thing that makes
  that visible. Offsets beyond ±15 m are rejected as sign or datum errors.
- **2.5 `bathymetry/emodnet.py`**: HR coverage discovery over an AOI's
  processing extent, `select_finest`, and `subset_to_cog` (elevation → depth
  sign flip, south→north row flip, land → NaN, provenance in GeoTIFF tags
  *and* a sidecar). Field lookup tolerates EMODnet's unstable WFS names.
- **2.6 `scene.py` + `acolite.py`**: `Scene` as the sensor-agnostic carrier
  (Rrs and rhos on one grid, with the solar-zenith provenance recorded), and
  ACOLITE driven as a subprocess with its own environment rather than
  imported — which is why it is not a declared dependency.

**Six genuine defects found by writing the tests.** Every one is a *silent*
failure mode, which is the same pattern Session 2 turned up, and the reason
the tests are written against behaviour rather than against the plan.

1. `_to_int("128")` returned **28**. `str.lstrip("1/")` strips every leading
   `1` and `/`, not the prefix `"1/"`. The Sesimbra HR LiDAR would have been
   reported as a 66 m grid instead of 14.5 m — coarser than the 115 m DTM's
   competitor in the `select_finest` ordering, so the pipeline would have
   silently preferred the wrong bathymetry. Replaced with an explicit prefix
   split plus a digit-run match, which also handles `128.0` and `2011-06-01`.
2. `Scene.from_arrays` synthesised `rhos = Rrs·π` when none was supplied, and
   set `rrs_derived_from_rhos` **inverted**. The identity only holds for a
   Lambertian, glint-free surface — precisely what deglinting exists to
   repair — so the SWIR screen and the glint correction would have been handed
   a fabricated raster indistinguishable from a measurement. `rhos` is now
   left absent and typed `np.ndarray | None`.
3. ACOLITE L2W `Rrs_*` output was not recognised as complete: `L2W_RRS_GLOB`
   was defined and never used. A run configured to emit only L2W Rrs would
   have been re-run on every retry, forever.
4. `Scene` accepted `rhos` of a different shape to `rrs_above`. They are the
   same bands over the same grid and every band index is shared between them,
   so a mismatch surfaces later as a wrong-band read rather than an error.
5. `Scene.rhos_band()` on a rhos-less scene raised a bare `TypeError`;
   a missing template gave `read_text`'s bare `FileNotFoundError`; and
   `run_acolite` checked the launcher before the settings file, so a missing
   settings file was reported as a missing ACOLITE install. All three now
   name the cause and the fix.
6. The provenance sidecar used `Path.with_suffix`, so `depth.tif` and
   `depth.nc` in one directory would overwrite each other's provenance.
   Appended instead of substituted.

**Verification state.**

- `pytest oceanstream/tests/unit/coastal`: **401 passed + 1 xpass** across
  16 modules. Phase 2 contributed 235: aoi 48, scene 39, sensors 29,
  rasters 29, tide 35, emodnet 33, acolite 22.
- `mypy oceanstream/coastal`: **clean on 28 files**.
- Ruff on `oceanstream/coastal` + its tests: 161 warnings, all three accepted
  baseline buckets (`PLR2004` 106, `PLC0415` 39, `PLR0913` 16) and nothing
  else. Filter with
  `ruff check … --output-format concise | grep -Ev 'PLC0415|PLR0913|PLR2004'`.
- Network paths (`discover_hr_areas`, `download_area`) are not exercised by
  the unit suite; only the pure helpers and `subset_to_cog` are, the latter
  against a synthetic 4×4 xarray tile.

**Still stubbed after Phase 2**: `processor.py` and `qc/floors.py`, and the
five CLI subcommands. Those are Phase 3.

## Session 4 (2026-09-10) — Phases 3.1 → 3.3, detectability + floor gates

**Landed.**

- **Phase 3.1**: `coastal/detectability.py`. `z_max_from_k` implements
  `ln(t_aw·Δρ_b/ε)/k`; `band_detectability` and `scene_detectability` wrap it
  per band and per scene. Band selection is genuinely scene-dependent — the
  Sesimbra fit yields 489 nm as best band with 444 nm close behind, while 561
  and 667 are marked unusable because their `k` sits below the pure-water
  floor. `SUBSTRATE_ALBEDO` carries the four-band Vahtmäe et al. (2024)
  bare/brown/red measurements, and every result carries the library DOI,
  citation and its three caveats (Baltic species measured out of water, no
  *Laminaria ochroleuca* or *Saccorhiza polyschides*; Q=π Lambertian
  assumption; boxcar FWHM band integration) so a downstream consumer cannot
  quietly treat Baltic albedo as Iberian truth.
- Each band reports `z_max_contrast_halved_m` and `z_max_epsilon_doubled_m`
  alongside the headline. z_max is only logarithmic in the assumptions but
  inverse-linear in `k`, so the sensitivity span is the honest number to
  quote — halving contrast moves 489 nm by 8.9 m.
- **Phase 3.2**: seabed PAR. `downwelling_kd` converts the two-way empirical
  `k` to `K_d` through `lee.two_way_to_downwelling_factor`;
  `interpolate_kd` fills the 400–700 nm grid by stripping pure water,
  interpolating only the residual and adding water back, so extrapolation
  past the last measured band cannot produce a `K_d` below physical.
  `seabed_par_fraction` integrates band-by-band (never materialising an
  `(n_λ, H, W)` cube) with photon weighting; `euphotic_depth` solves the
  profile it is defined on.
- **Phase 3.3**: `qc/floors.py` — `pure_water_floor_check`,
  `lyzenga_ratio_check`, `scene_floor_verdict`. Neither gate needs ground
  truth, which is the property that makes them deployable at 683 AOIs.
  Both read `BandCalibration.k_per_m`, never `k_effective`: the latter is
  already floored, so a gate reading it would certify every scene.
- Config: `DetectabilityConfig` added; `QCConfig` gained five floor-gate
  fields (`k_floor_slack`, `floor_solar_zenith_deg`, `ratio_contrast_max`,
  `ratio_unity_tolerance`, `ratio_floor_slack`).
- `lee.py` gained `kd_pure_water` and `two_way_to_downwelling_factor`.

**The pure-water floor in this plan was understated — correct it.**

This plan records "k(667) = 0.077 vs pure-water floor 0.43 → 5.6× violation"
and Phase 5 sets acceptance at "k(667) ≥ 0.43". **0.4303 is `a_w(667)` alone.**
The floor that applies to a two-way empirical `k` is the full Lee expression —
`(a_w + b_bw)·(1/cos θ_w + D_u^B)` — which is **0.9257 m⁻¹** at 35°. So:

- The real 667 nm violation is **12×, not 5.6×**.
- **561 nm also fails** (k = 0.0802 vs floor 0.1388). The prototype's
  `MEASURED_KB` had treated 561 as valid and excluded only 667, so the
  tighter floor rejects one more band than the prototype did.
- The Phase 5 acceptance target should read **k(667) ≥ 0.926**, not 0.43.

`BandCalibration.k_pure_water_floor` already used `lee.kb_pure_water` from
Phase 1.5, so the library was self-consistent — only the plan text was wrong.

**The two gates are not redundant, and Sesimbra proves it.** The Lyzenga
489/667 ratio is 1.008 where pure water demands 0.042 — that is 24× *above*
the floor, so `below_pure_water` does **not** fire. Only the soft
`near_unity` flag catches it. A depth-correlated artefact that inflates every
band equally leaves each individual floor intact while pinning the ratio at
unity, and that is exactly the failure mode here.

**One defect found by testing.** The `two_way_to_downwelling_factor`
docstring claimed the factor rises with solar zenith. It falls — 2.046 at
nadir, 1.945 at 35°, 1.828 at 55° — because a lower sun lengthens the
downward path, so `K_d` grows against a fixed upward `D_u^B` term and the
ratio moves towards 1. It does rise with `u` (2.356 at u = 0.2, 35°).
Corrected in the docstring and in the test.

**Measured scene state (Sesimbra 2026-06-27, ε = 0.003659, zenith 21°).**

```
scene_floor_verdict: passed=False
  "2 band(s) below the pure-water floor (worst 667 nm at 0.08x);
   k ratio near unity for 489/667, 561/667 (common-mode additive artefact)"
scene_detectability: status=ok  best=489 nm  z_max=41.42 m
  444  k=0.1085 empirical        Δρ=0.1596  zmax=28.77  span=6.39  usable
  489  k=0.0778 empirical        Δρ=0.1766  zmax=41.42  span=8.91  usable
  561  k=0.1345 floored          Δρ=0.1904  zmax=24.53  span=5.15  not usable
  667  k=0.8961 floored          Δρ=0.2286  zmax= 3.88  span=0.77  not usable
```

The 41 m z_max is not a claim about Sesimbra. It is what the *current*
(floor-violating) fit implies, and the floor verdict next to it says the fit
is not trustworthy. That juxtaposition is the intended product behaviour:
coverage and usability are separate maps, and the QC verdict travels with the
number.

**Verification state.**

- `pytest oceanstream/tests/unit/coastal`: **487 passed** across 18 modules.
  Phase 3 contributed 85 (detectability 59, floors 26).
- `mypy oceanstream/coastal`: **clean on 29 files**.
- Ruff: 179 warnings, all three accepted buckets (`PLR2004` 120,
  `PLC0415` 42, `PLR0913` 17) and nothing else.
- `test_floors.py` asserts the 667 nm violation directly, so the known-bad
  scene is now a regression trap — a future loosening of the gate that lets
  Sesimbra through will fail the suite.

**Still stubbed after Phase 3.3**: `processor.py`, `stac/coastal_emit.py`
(not yet created) and the five CLI subcommands. Those are Phase 3.4.

## Where this sits — three layers, three owners

```
EarthStudio (sar-watch)              OceanStream → EDITO
──────────────────────              ────────────────────
AOI registry  ← system of record
Scene discovery (CDSE / PC / VHR)
Products feed
        │  /v1/detect  or  feed poll
        ▼
                              Prefect flow: coastal_detect
                              └─ oceanstream.coastal  ← THIS PLAN
                              Dask on EDITO K8s
                              MinIO S3 + STAC
        ◄── HMAC webhook ─────
Alerts, change detection, UI
                              arco-3d globe: POST /api/ingest/cogs
```

| Layer | Where | Status |
|---|---|---|
| **Library** `oceanstream.coastal` | `sd-data-ingest` | this plan |
| **Flow** `coastal_detect.py` | `os-webapp/server/flows/` | follow-on |
| **Service** | EDITO: Prefect + Dask + MinIO | follow-on |

EarthStudio provides the **AOI registry and scene discovery**, not the compute
plane. Per `kelp_observe/.github/copilot-instructions.md`: os-webapp is "the
compute plane"; the ES provider-contract endpoints exist but "the detector runs
as a Prefect flow".

The library must stay ignorant of all of it — no EarthStudio import, no Prefect
import, no Azure/MinIO assumption. Outputs go through
`storage.filesystem.resolve_output_path()` so `az://`, `s3://` and local all work.

**AOI coupling**: EarthStudio issues AOI UUIDs (Sesimbra =
`a3d3ba9f-d30f-4f14-9df1-3f9f444e60e7`). Keep the library's `AOI` a plain
dataclass; add `AOI.from_earthstudio()` as an optional adapter in the acquire
extra. Library runs on a hand-typed AOI; service runs on registry AOIs.

**Phase 7 option (not committed)**: register the coastal service as an
EarthStudio detection provider via `/v1/detect` + HMAC webhook. Both sides of
that contract already exist — it is wiring, not building. Only pays off once the
library and flow are stable, and it is reversible.

## Key research findings

**Target — sd-data-ingest**

- No `oceanstream/coastal/` (nor `benthic/`) — greenfield.
- Poetry extras via `[tool.poetry.extras]` (NOT PEP 621 `[project.optional-dependencies]`).
- `echodata` is the template: `processor.py` + `config.py` + algorithm subpackages,
  lazy `__getattr__` in `__init__.py` to avoid heavy imports.
- CLI: `process_app.add_typer(coastal_app, name="coastal")` in `oceanstream/cli.py`.
- Storage: `oceanstream/storage/filesystem.py` → `resolve_output_path()`,
  `parse_storage_uri()`, `StoragePath` (PyArrow fs abstraction).
- STAC: mirror `echodata/stac/echodata_emit.py`, NOT the geotrack GeoParquet emitter.
- Tests: `oceanstream/tests/unit/coastal/`, `integration/test_cli_coastal_*.py`,
  marker `integration`, `CliRunner` pattern.
- `ProcessingModule` Literal in `providers/base.py` needs `"coastal"` added.
- Style: ruff line 100, mypy strict, `from __future__ import annotations`, pathlib.

**Source — lee_demo** (57 files: 38 modules + 19 tests)

- CORE portable: `water.py` 140, `qaa.py` 392, `lee.py` 260, `bathymetry.py` 545,
  `masks.py` 176, `classify.py` 170.
- Diagnostics worth porting: `reef_calibration.py` 826, `ac_uncertainty.py` 207,
  `sand_control.py` 251, `point_diagnostic.py` 319, `sdb_comparison.py` 268.
- DROP: 6 publishing files, 4 figure/report files, 5 CCMAR-specific files.
- `SiteProfile` dataclass exists (20 fields, 2 sites) but only `run_demo` uses it;
  `batch.py` + all diagnostics hardcode Sesimbra.
- **BUG: `qaa.kd_map` is broken** — `a_band` NameError at qaa.py#L388-L392.
- ACOLITE runs as subprocess: `launch_acolite.py --cli --settings`.
- Deps: numpy, scipy, sklearn, pandas, xarray, rasterio, geopandas, shapely,
  pyproj, requests, copernicusmarine, pystac_client, planetary_computer.

**Measured facts to preserve as regression values**

- Sesimbra 2026-06-27 empirical k: 444=0.10848, 489=0.07775, 561=0.08018, 667=0.07720.
  Reproduces exactly; independent of QAA. Use as golden test.
- QAA overestimates k by 1.6–5.3× (median ratio 0.188 / 0.469 / 0.627 for the 3 scenes).
- k(667)=0.077 vs pure-water floor **0.926** → **12×** violation. THE BLOCKER.
  (0.43 is `a_w(667)` alone; the floor for a two-way `k` is the full Lee
  expression — see Session 4. 561 nm fails against it too: 0.080 vs 0.139.)
- Lyzenga 489/667 ≈ 1.007 → common-mode depth-correlated artefact.
- z_max ≈ 12–16 m measured (bottom signal plateaus).
- AC effective threshold: 0.003659 rhos (06-27, 100 m blocks) / 0.000604 (other scene).
- EMODnet HR coverage Atlantic Europe: 835 areas, 683 ≤29 m, 341 ≤14 m.
  Ireland 403 (217 fine), Scotland 68 (all fine), Iberia 59, Brittany 10 (1 fine).

## Phases

### Phase 0 — Unblock (prerequisite, small) ✅ 2026-09-09

- 0.1 ✅ `git add tools/lee_demo/ outputs/diagnostics/` — 39 py + 11 JSON
  currently untracked. 72 files (22,803 LOC) staged; commit deferred to
  end of session. `__pycache__/`, `.pytest_cache/`, and the 2 GB
  `tools/lee_demo/.cache/` added to `.gitignore`.
- 0.2 ✅ Fixed `qaa.kd_map` NameError — a comment on qaa.py line 400 had
  absorbed the trailing `a_band = …` assignment. Repro verified before fix.
- 0.3 ✅ Pinned `qaa_scene_iops.json` path in `reef_calibration.py`. The
  saved report now carries `qaa_iops_source = {path, solar_zenith_deg}`
  alongside `water_mass_mismatch` and `bottom_signal`, so any `usable`
  verdict is directly traceable to the QAA fit it depends on.
- 0.4 ✅ Exposed `K_d(560)` in the driver log line
  (`run_demo.py` prints `K_d(490)=… K_d(560)=…`). The full `kd` array
  was already in the serialised report.

### Phase 1 — Extract core physics ✅ 2026-09-10 (1.6 partial — see below)

Module layout (mirrors echodata conventions):

```
oceanstream/coastal/
  __init__.py          lazy __getattr__ public API
  config.py            RetrievalConfig, MaskConfig, AttenuationConfig dataclasses
  aoi.py               AOI dataclass (place) — replaces SiteProfile
  sensors.py           SensorProfile registry — SENTINEL2, PLEIADES_NEO
  scene.py             Scene — sensor + date + solar geometry + raster paths
  processor.py         CoastalProcessor orchestration → CoastalResult
  optics/  water.py, qaa.py, attenuation.py
  inversion/  lee.py
  bathymetry/  stumpf.py, terrain.py, emodnet.py, tide.py
  masks.py
  detectability.py     NEW — z_max, seabed PAR
  qc/  ac_uncertainty.py, floors.py, sand_control.py
  classify.py
  acolite.py           subprocess runner
  acquire/             OPTIONAL extra — cdse.py, planetary.py (convenience only)
  stac/  coastal_emit.py
```

- 1.1 ✅ Ported `water.py` → `coastal/optics/water.py` and `lee.py` →
  `coastal/inversion/lee.py` (renamed `invert_rho_b` → `invert_scene`;
  promoted `forward_model` to public API; `invert_rho_b` alias retained).
  Ported the numerical test suite as `tests/unit/coastal/test_lee_inversion.py`
  with a bundled Sesimbra 2026-06-27 IOP fixture — 19 passed + 1 xpass,
  matches prototype behaviour byte-for-byte.
- 1.2 ✅ Ported `qaa.py` with the `kd_map` fix. `RRS_670_TURBID_THRESHOLD` →
  `RetrievalConfig.rrs_670_turbid_threshold`. `A_CDM_443_MIN` became a
  two-tier pair — `a_cdm_443_max_fatal` (−0.03, reproduces the prototype's
  flag verbatim) and `a_cdm_443_min` (−0.005, new soft AOI bound) — because
  the prototype's `SiteProfile.a_cdm_443_min` was dead code. 19 tests.
- 1.3 ✅ Ported `masks.py`, `classify.py`, `bathymetry/stumpf.py`. `MaskConfig`
  rewritten, `BathymetryConfig` added. Cluster labels are `spectral_class_N`,
  never habitat names; Stumpf accuracy is absolute error per depth stratum,
  never R². 54 tests. Fixed a latent defect: `adjacency_buffer(buffer_px=0)`
  masked the whole scene (scipy reads `iterations=0` as "dilate to
  convergence").
- 1.4 ✅ Promoted `sand_control.terrain_classes()` → `bathymetry/terrain.py`.
  Five thresholds into `BathymetryConfig`. `flat` and `rugose` deliberately
  do not partition the scene. 14 tests. Fixed a latent defect: NaN-depth
  pixels were classified `flat` and so became positive "sand" controls
  (`np.gradient` skips the centre pixel; scipy min/max filters do not
  propagate NaN).
- 1.5 ✅ Extracted `reef_calibration._fit_one_band` + `calibrate` →
  `optics/attenuation.py` as `fit_band_attenuation` + `calibrate_bands`,
  with `BandCalibration`, `deep_water_reference` and `lyzenga_ratios`.
  `AttenuationConfig` had to be rewritten — its Phase-1.0 placeholder values
  did not match the prototype and would have made the golden regression
  measure something else entirely. 21 tests.
- 1.6 ✅ Ported `ac_uncertainty.py` and `point_diagnostic.py` into `qc/`, with
  a new `QCConfig`. Both generalised off the prototype's hard-coded 10 m
  Sentinel-2 grid: block sizes configured in metres, converted via a
  `pixel_size_m` argument. 28 tests.
  **Partial:** only `terrain_classes` was taken from `sand_control.py` (in
  1.4). Its substrate-control diagnostic — `_summarise`, `_depth_trend` and
  the pre-registered constants (`PREDICTED_BARE_RHO_B`,
  `MIN_BRIGHTNESS_RATIO`, `MAX_DEPTH_TREND_PER_M`, `DEPTH_BINS_M`) — is not
  yet ported, and neither is `reef_calibration.compare_with_qaa`. Both are
  wanted for Phase 5; `qc/sand_control.py` in the layout above is still
  vacant. The pre-registered constants must be carried across unchanged —
  the prototype warns they were fixed in advance and must not be tuned to
  fit the result.

### Phase 2 — Generic AOI + Sensor + data adapters

**Design correction (2026-09-09).** The prototype conflates *place* and
*instrument* — `sites.py` carries `solar_zenith_fallback_s2_deg` / `_other_deg`,
but solar geometry is a SCENE property and band layout is a SENSOR property,
neither belongs in a site. Three orthogonal objects:

- `AOI` — geography: bbox, CRS, bathymetry URI, deep-water polygon, tide model.
- `SensorProfile` — band name→wavelength map, native GSD, ACOLITE sensor key,
  `deglint_reference_nm`, `has_swir`, `deepwater_screen_bands`, `null_channel_nm`.
- `Scene` — one acquisition: sensor + date + solar zenith/azimuth + raster paths.

- [x] 2.1 `aoi.py` — `AOI` dataclass. NO project-relative Path defaults in core.
- [x] 2.2 `sensors.py` — `SensorProfile` registry. Ship `SENTINEL2` and `PLEIADES_NEO`.
- [x] 2.3 `bathymetry/emodnet.py`: query `emodnet:hr_bathymetry_area` WFS
  (`https://ows.emodnet-bathymetry.eu/wfs`) → intersect AOI → pick finest
  `resolution` → fetch `download_url` → reproject to scene grid.
  Makes AOI onboarding one call, not manual.
- [x] 2.4 `bathymetry/tide.py`: NEW. LAT→instantaneous datum correction. Sesimbra
  regression is `hr = 1.013*diver − 1.48`; per-date offsets −1.92/−1.09/−0.58 m
  track tide. Global model needed (FES2022 or CMEMS) for scale.
  NOTE: constant offset is harmless for depth-invariance tests, matters for z_max.
  Shipped with `ConstantTide` / `NullTide` and a `PyTMDTide` placeholder; the
  global model is deferred until an AOI needs it.
- [x] 2.5 `acolite.py`: wrap the subprocess runner; sensor key from `SensorProfile`;
  make ACOLITE path configurable and the dependency optional with an actionable
  ImportError.
- [x] 2.6 `io/rasters.py`: COG read/write, grid alignment, reprojection.
  Plus `scene.py` — the `Scene` carrier the three objects above feed into.

#### Scene acquisition — OUT of the library core

The library input contract is: `Scene` (ACOLITE surface-reflectance rasters +
metadata) + `AOI` + `RetrievalConfig`. How the scene arrived is not its business.

**Do not reimplement discovery.** EarthStudio (`pineviewlabs/sar-watch`) already
owns this and has more of it than lee_demo does:

- `SceneDiscoveryProvider` protocol + `pipelines/providers/registry.py`
- S2 L1C and L2A, CDSE default with Planetary Computer optional
- **PNeo DIMAP bundles already handled** — `pipelines/pneo_processing.py`,
  `routers/vhr_upload.py`
- Downloads already land at `az://raw/s2_l1c/{tile}/{date}.SAFE.zip`
- Per-AOI `discovery_providers` JSONB, and `earthstudio_sync.py` in os-webapp
  already polls the products feed

So `fetch_s2_l1c.py` (229 LOC) and `pick_dates.py` (333 LOC) are duplicating a
built system. Port them as an OPTIONAL convenience subpackage only:
`coastal/acquire/{cdse.py,planetary.py}` behind the `coastal-acquire` extra, for
standalone/CLI/test use. Production path is EarthStudio → scene URI → library.
`batch.py` (490 LOC) is orchestration — drop it; Prefect owns that later.

#### PNeo support

Core physics is already sensor-agnostic: `water.py`, `lee.py` are functions of
(wavelength, a, bb, H). `run_demo` is already a "one-sensor or two-sensor"
pipeline and `sites.py` already carries an `_other_deg` solar fallback for PNeo.

**What actually breaks — all of it SWIR-related, because PNeo has no SWIR:**

| Concern | S2 | PNeo | Consequence |
|---|---|---|---|
| Deglint reference | B11 1612 nm | none | Must fall back to NIR ~865. Hedley assumes zero water-leaving in the reference band; NIR is NOT zero over bright shallow sand → over-correction risk. Already a known caveat at Ria Formosa. |
| Deep-water screen | `swir_bands_nm=[1612,2191], swir_max=0.01` (dropped 62503→5369 px) | none | Needs an NIR-based or depth-based screen. |
| ACOLITE AC | dark-spectrum fitting well constrained | weaker without SWIR | Accept higher AC residual; ε will be larger. |
| Null channel | 667 nm (a_w=0.43) | Red ~660 **or Red Edge ~710** (a_w≈0.8) | Transfers fine — red edge is an even *better* null. |
| QAA reference selection | tuned on 443/490/555/670 | approximate equivalents | S2-tuned coefficients on PNeo bands is an approximation; flag it. |
| GSD vs bathymetry | 10 m img / 11.4 m lidar | **1.2 m img / 11.4 m lidar** | Inverts the ratio. Substrate homogeneity gets *better*, depth assignment gets *worse*. Aggregate PNeo to the lidar grid for the k regression. |

**PNeo has a designed role already**: `reef_calibration.py:51-53` says confirming
the tracked endmember is one substrate "needs the 0.3 m PNeo panchromatic (plan
step 1.C), which is not yet done". So PNeo validates the substrate-homogeneity
assumption that the empirical k depends on. Keep that as a Phase 5 item.

**Scope**: build `SensorProfile` for both from day one so the abstraction is
exercised, but validate S2 first. PNeo scenes are scarce (tasking pending) and
should not gate the 4-AOI validation.

### Phase 3 — Detectability products (the deliverable)

- 3.1 ✅ `detectability.py`: z_max = ln(Δρ_b·t_aw/ε)/k per band per scene.
  ε from `qc/ac_uncertainty` (spatially-varying scatter, NOT the uniform offset).
  Band selection per scene — audit confirms usability is scene-dependent
  (clean scene → blue passes, hazy → green).
- 3.2 ✅ Seabed PAR: E_d(z) = E_d(0⁻)·exp(−Kd·z). GOTCHA: empirical k is TWO-WAY;
  halve (approximately) or derive Kd properly. Integrate across bands for PAR.
  Ported as `downwelling_kd` — the halving shorthand is off by up to 25%.
- 3.3 ✅ `qc/floors.py`: pure-water floor check + Lyzenga ratio check as reusable gates
  returning a per-scene verdict. These need NO ground truth — that is what makes
  the product deployable at 683 AOIs.
- 3.4 ⬜ Outputs: z_max COG, k(λ) JSON, seabed-PAR COG, SDB COG, rho_b COG (labelled
  "effective benthic reflectance", not a material property), cluster COG,
  STAC item carrying the QC verdict.

### Phase 4 — Multi-AOI validation

Four AOIs chosen for OPTICAL DIVERSITY and for actually having kelp.
Algarve dropped: no kelp, mobile sediment, hardest case for least value.
Norway ruled out on coverage — only 6 HR areas in the whole 4–12E/57–65N box,
1 fine. Skarvøya almost certainly has no tile. High solar zenith + L. hyperborea
to 30 m also puts most of it below z_max.

| AOI | Provider | Optical regime | Why |
|---|---|---|---|
| Sesimbra | EDMO 590, 1/128 | Clear, near-zero CDOM (fDOM 0.05 QSU, sal 36.7) | Baseline; golden k values |
| Ireland NW (Donegal) | INFOMAR EDMO 366 | Turbid / mixed | 403 areas, 217 fine. Use Lough Swilly/Foyle-type NW tiles, NOT Celtic Sea (Cork/Waterford have poorer kelp) |
| Scotland NW | BGS, 1/128 | **High CDOM** — peat-stained sea lochs | 68 areas, ALL fine. Named surveys on real L. hyperborea coast: Summer Isles, Loch Eriboll, Firth of Lorn |
| Galicia *(conditional)* | within the 59 Iberian areas — VERIFY | Iberian upwelling, same regime as Sesimbra | Same species as classifier `submerged` models (L. ochroleuca, Saccorhiza). BioCost/A Coruña already in `spain/_literature.yml`. **Drop to 3 AOIs if no tile exists.** |

Scotland is the key addition: high-CDOM water is the opposite failure mode from
Sesimbra and directly stresses the QAA reference-band selection — exercising the
`red_reference_selected` flag that is already the most diagnostic symptom.

- 4.1 Run empirical attenuation + QC suite at all AOIs.
- 4.2 **Decision gate**: does the 667 floor violation + ~1.0 Lyzenga ratio reproduce
  everywhere, or vary by water type? This is the evidence Phase 5 needs.
- 4.3 Report z_max distribution per AOI. Coverage and usability are different maps.

### Phase 5 — Fix the 667 nm additive offset (in the library, evidence-led)

Deliberately AFTER multi-AOI, because multi-AOI data is what distinguishes a
systematic AC-chain bug from a site-specific artefact.

- 5.1 If systematic across all four → single fix in the AC/offset chain.
- 5.2 If varying by water type → per-AOI QC required; absolute k stays bracketed and
  only the relative clarity ranking ships.
- 5.3 Separate additive offset from underestimated column term. `ac_uncertainty.py`
  caveat: inseparable in red because the column term saturates by 4 m. Needs a
  joint fit across 444/489/561 with 667 held as the null channel.
- 5.4 Acceptance: k(667) ≥ pure-water floor **0.926** *and* k(561) ≥ 0.139
  (`lee.kb_pure_water`, not `a_w`); Lyzenga k(489)/k(667) ≈ 0.042 not 1.0.
  Equivalently: `qc.floors.scene_floor_verdict` returns `passed=True`.
- 5.5 **Re-baseline the golden test** — see Verification note.
- 5.6 Detectability ships regardless: z_max from the observed plateau is model-free.

### Phase 6 — CLI + packaging

- 6.1 ✅ `[tool.poetry.extras]` `coastal = [...]`, `coastal-acquire = [...]`,
  both included in `all`. Landed as part of the Phase 1.0 skeleton.
- 6.2 ✅ `coastal_app` Typer group under `process` with the five
  subcommands stubbed (`NotImplementedError` per phase). Wire-up in
  `oceanstream/cli.py`. Conventions applied: kebab-case flags,
  `-o/--output-dir`, `-v`, `--yes`, `--dry-run`.
- 6.3 ✅ Added `"coastal"` to `ProcessingModule` Literal in `providers/base.py`.
- 6.4 `oceanstream/coastal/README.md` — TBD after Phase 1.6.
- 6.5 Update the three docs carrying the old module name (see Naming section) — TBD.

## Relevant files

**Read/port from**

- `kelp_observe/tools/lee_demo/{water,qaa,lee,masks,classify,bathymetry}.py` — core
- `kelp_observe/tools/lee_demo/reef_calibration.py` — `_fit_one_band`, `calibrate`,
  `deep_water_reference`, `BandCalibration.k_pure_water_floor`
- `kelp_observe/tools/lee_demo/sand_control.py` — `terrain_classes()`
- `kelp_observe/tools/lee_demo/ac_uncertainty.py` — `effective_threshold_rhos`
- `kelp_observe/tools/lee_demo/sites.py` — `SiteProfile` → generalise to `AOI` + `SensorProfile`
- `kelp_observe/tools/lee_demo/tests/test_lee_inversion.py` — portable numerical tests

**Write to**

- `sd-data-ingest/oceanstream/coastal/**` — new module
- `sd-data-ingest/oceanstream/cli.py` — register `coastal_app`
- `sd-data-ingest/pyproject.toml` — `[tool.poetry.extras]` coastal
- `sd-data-ingest/oceanstream/providers/base.py` — `ProcessingModule` Literal
- `sd-data-ingest/oceanstream/tests/unit/coastal/`, `tests/integration/`

## Verification

1. Port `test_lee_inversion.py` — synthetic forward/inverse roundtrip recovers
   supplied ρ_b to ~7.5e-10. Must still pass.
2. Golden regression: Sesimbra 2026-06-27 empirical k(444) == 0.10848 ± 1e-4.
   **Semantics matter.** Because we port before fixing 667, this asserts *faithful
   reproduction of the prototype*, not correctness — it is a refactor safety net
   over a known-biased value. Must be re-baselined after Phase 5, and the test
   should say so in its docstring so nobody mistakes it for a physics assertion.
3. `pytest -m "not integration"` green; `ruff check`; `mypy oceanstream/coastal`.
4. CliRunner integration test: `process coastal detect --aoi … --output-dir …`
   exits 0 and writes a STAC item with a QC verdict.
5. QC gates fire correctly: assert `pure_water_floor_violated` is True for the
   current Sesimbra 667 nm fit (regression on a known-bad case).
6. Multi-AOI run produces a comparison table of k, z_max, and QC verdicts.

## Decisions

- **Port first, fix 667 in the library** (user, 2026-09-09). Enough time spent in the
  prototype. Consequence: diagnostics move onto the critical path (1.6), and the
  golden test is a refactor net rather than a physics assertion.
- **Algarve dropped** (user): no kelp, mobile sediment — hardest case, least value.
  Port the existing profile for free but do not invest until the others are stable.
- **Norway ruled out on data**: 6 HR areas total in 4–12E/57–65N, 1 fine.
- **In scope**: standalone library, generic AOI, detectability products, 3–4 AOI validation.
- **Out of scope**: EDITO deployment, ML/classifier integration (separate library),
  publishing to HabitatExplorer/arco-3d, figures/slides, all CCMAR field-data tooling.
- Physics stays label-free by design — that is the product's advantage.
- `rho_b` ships as "effective benthic reflectance", never as a material property.
- Do NOT scale to 683 AOIs before the Phase 4 decision gate.

## Open

- Galicia HR tile existence UNVERIFIED — 59 areas in the Iberia box (-10.5..-6E,
  36..44N) but Galicia-specific count not yet queried. Re-run the
  `/tmp/hr_area.json` filter for (-9.6, 42.0, -7.5, 44.0) before committing to it.
- ACOLITE packaging: optional dep (user installs) vs containerised (like
  `oceanstream/mohid:24.10`) vs pre-corrected-input-only. Leaning optional dep
  now, container for EDITO.
- Phase 7 option, not committed: register the coastal service as an EarthStudio
  detection provider via `/v1/detect` + HMAC webhook.
- ✅ Golden regression (Verification §2) wired 2026-09-10 in
  `tests/unit/coastal/test_golden_sesimbra.py`. Reproduces the prototype to
  ~1e-8 on all four bands, plus the sensitivity sweep and Lyzenga ratios.
- Still unported from the prototype, wanted for Phase 5:
  `sand_control`'s substrate-control diagnostic and
  `reef_calibration.compare_with_qaa` (empirical reef k vs offshore QAA k — a
  direct measure of the water-mass mismatch that no other diagnostic
  isolates). Also unported and lower value: `bottom_signal_by_depth`,
  `resolve_additive_offset`, `screen_deepwater`, `deepwater_additive_offset`.
- Pre-existing baseline noise discovered during Phase 1.1, unrelated to the port
  but worth flagging so the check "pytest -m 'not integration' green" in
  Verification §3 has a fair reference:
  - `oceanstream/tests/unit/test_s3_storage.py` collides with
    `oceanstream/tests/unit/echodata/test_storage.py` on basename resolution
    (pytest emits a duplicate-module hint).
  - `oceanstream/tests/unit/echodata/test_storage.py` has collection errors on
    `TestLocalStorage` (`test_local_list_fs_isdir`,
    `test_local_list_fs_nonexistent`, `test_local_list_fs_get_mapper`,
    `test_get_azure_filesystem_local_mode`, `test_local_storage_exports`).
  Neither is triggered by any coastal change; fix outside this plan.
