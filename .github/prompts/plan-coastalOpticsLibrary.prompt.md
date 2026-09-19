# Plan: `oceanstream.coastal` — coastal optics library

> **2026-09-15 implementation update:** the scientific-beta engineering changes
> supersede the historical completion claims below. See
> [current status](../../docs/coastal/beta-status.md) and
> [library/CLI documentation](../../oceanstream/coastal/README.md).
> Product-specific acceptance, independent depth preparation, local region masks,
> tide/datum checks, isolated runs, shared processing stages, benchmark tooling,
> and clean-wheel checks are implemented. The two-site scientific release gate
> remains open; historical golden outputs are regression evidence only.
> Supplied inputs are now connected through a frozen six-scene diagnostic
> registration. See [benchmark results](../../docs/coastal/six-scene-benchmark.md)
> and [staged field-validation options](../../docs/coastal/field-validation.md).


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

Session 6 of the port. **Phases 0, 1, 2, 3, 4.1, 4.2, 5 (ported) and 6.1–6.3 are
landed.** The library runs end to end, is driveable from the CLI, finds its own
bathymetry and scenes at an AOI it has never seen, and has been run over five
scenes at three AOIs.

**The 4.2 gate is answered and the answer is not the one the plan expected.**
The 667 nm floor violation reproduces at every site, but the *cause* does not
transfer: only Sesimbra produces a valid fit. At both northern sites the best
regression in the scene lands at a wavelength where pure water extinguishes the
bottom within a metre, which means the fit is tracking a horizontal
depth-correlated gradient rather than vertical attenuation. Two candidate
explanations were tested and eliminated this session — a truncated deep-water
reference (fixed at Donegal; fit still invalid) and a residual additive offset
(ported, measured, and shown to cancel exactly in the differential fit). What
remains is the homogeneity assumption itself.

**Next slice** is decoupling the deep-water reference extent from the attenuation
fit extent — the reference needs a wide footprint, the fit needs a homogeneous
one, and the two are currently the same box. Then `sand_control`, then 4.3, then
the remaining Phase 6 documentation.

The section below records Session 1; each later session has its own section.

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

## Session 5 (2026-09-10) — Phase 3.4, products, STAC, processor, CLI

Phase 3 is complete. `oceanstream.coastal` now runs end to end: an AOI and an
ACOLITE directory go in, a directory of COGs, JSON documents and a STAC item
come out, and `oceanstream process coastal detect` drives it from a shell.

**Landed.**

- `coastal/products.py` — product catalogue (`ProductSpec`) and `ProductWriter`.
  Seven raster products (`rho_b_<nm>`, `sdb_depth`, `spectral_class`,
  `detectability_margin`, `detectable`, `seabed_par`, `optical_depth`) and five
  JSON documents (`attenuation`, `detectability`, `iops`, `qc`, `report`).
- `coastal/stac/coastal_emit.py` — item + collection builders, geographic
  bounds via `transform_bounds` for projected grids, and `emit_stac`.
- `coastal/processor.py` — the ten-stage `CoastalProcessor.run`.
- `cli.py` — all five subcommands implemented; exit codes 0 / 1 / 2 / 3 for
  passed / failed / insufficient data / QC-rejected.

**Three structural decisions, argued in the `products.py` docstring.**

1. The QC verdict is a **constructor argument** of `ProductWriter`, not an
   optional extra. There is no way to write a product without having decided
   whether the scene is admissible, so a failed scene tags every output
   `diagnostic_only` and cannot be quietly published.
2. `rho_b` is labelled **"effective benthic reflectance"** everywhere, with the
   caveat carried in the GeoTIFF tags rather than a sidecar that can be lost in
   a copy. It is not a material property; it absorbs every unmodelled term.
3. Cluster indices are **spectral classes, never habitat names**. Naming a
   cluster "kelp" is the single easiest way to turn an unsupervised convenience
   into a false ecological claim.

**Deviation from the Phase 3.4 bullet.** The bullet asks for a "z_max COG".
There isn't one, deliberately: `k(λ)` is fitted per scene, not per pixel, and
ε is scalar, so a z_max raster would be a constant image pretending to be a
map. What ships instead is `detectability_margin` (z_max − depth) and
`detectable` (boolean), both of which *do* vary spatially, with the scalar
`z_max_m` in `detectability.json` and in both rasters' tags.

**`classify_bottom` no longer aborts a run.** It raises `ValueError` when too
few pixels survive the all-bands-finite gate. Previously that propagated out of
stage 8 and killed the run with `execution_failed`, discarding depth,
attenuation, detectability and rho_b — everything stages 1–7 had already
computed — for the sake of a map nobody is obliged to use. The processor now
catches it, logs, skips the `spectral_class` product and records the reason
under `clusters.skipped` in the report.

### Four defects found by writing the tests

**1. GDAL silently destroys colon-namespaced raster tags.** Writing
`tags={"oceanstream:aoi": "sesimbra", "plain": "v"}` reads back as
`{'oceanstream': 'aoi=sesimbra', 'plain': 'v', 'AREA_OR_POINT': 'Area'}` —
GDAL treats `:` as a metadata-*domain* separator, collapses every namespaced
tag into a single `oceanstream` key and keeps only the last value. The
`rho_b` caveat, the QC status and the provenance were all being written and
then thrown away, without an error. Fixed with `_raster_tag()` (→
`OCEANSTREAM_*`) plus a guard in `write_cog` that refuses any tag key
containing `:`. Colon namespacing is still correct — and still used — in JSON
and STAC properties. Two regression tests pin it.

**2. `Scene.sensor` was being re-resolved.** `processor.py` called
`get_sensor(scene.sensor)`, but `Scene.sensor` is already a `SensorProfile`
(`scene.py:59`; both `from_arrays` and `from_acolite_dir` resolve it). Result:
`AttributeError: 'SensorProfile' object has no attribute 'strip'` on **every
real run**. The same mistake sat in `emit_stac`, which also passed a
`datetime.date` where an ISO string was wanted.

**3. Asset hrefs were machine-local absolute paths.** `_relative_href` used
`Path.relative_to`, which only descends. Items live at
`<root>/stac/items/`, two levels *below* the rasters at `<root>/`, so every
call raised and fell back to the raw absolute path — a catalogue that only
resolved on the machine that wrote it. Now `os.path.relpath`, with cloud URIs
passing through unchanged.

**4. `CoastalResult.scene_date` was annotated `str | None`** while being
assigned a `datetime.date`. Caught by mypy once the processor was wired.

### Building a synthetic scene taught us something real

The test scene is generated forward through `forward_model` from prescribed
rho_b and depth using the saved Sesimbra IOP fit. Two things had to be fixed
before the pipeline would run on it, and both are physics, not test plumbing:

- **The generating IOPs had to be floored at pure water.** The saved Sesimbra
  fit sits *below* the pure-water absorption floor at 667 and 707 nm — the very
  additive-AC bias Phase 5 exists to correct. Generating Rrs from it produces
  radiances no water column can make, and the retrieval then correctly refuses
  to invert them, yielding zero finite `rho_b` in the red.
- **A single linear depth ramp cannot satisfy both ends of the pipeline.** The
  QAA fit needs optically deep water (z > 30 m *and* z·Kd(490) > 6, so past
  ~70 m at Sesimbra); the bottom retrieval needs water shallow enough for red
  light to make the round trip. The transect is now a 22 m shelf over 40
  columns with a drop to 140 m over the last eight. This is a real constraint
  on operational scenes, not an artefact: an AOI drawn entirely over a shallow
  shelf has no deep-water anchor for the fit.

**Verification state.**

- `pytest oceanstream/tests/unit/coastal`: **573 passed + 1 xpass** across 21
  modules. Phase 3.4 contributed 86 (products 28, STAC 23, processor 35).
- `mypy oceanstream/coastal`: **clean on 31 files**.
- Ruff: same three accepted buckets, plus `PLR0911`/`PLR0912` on the run
  dispatcher and the `jsonable` type switch — both pre-existing patterns
  (96 instances tree-wide).
- CLI integration tests use `CliRunner` against a real ACOLITE-shaped
  directory: `detect` exits 0 and writes a STAC item carrying the QC verdict;
  `--fail-on-reject` turns the Sesimbra floor violation into exit 3;
  `--dry-run` writes nothing.

**Next slice** is Phase 4 (multi-AOI validation).


## Session 6 (2026-09-10) — Phase 4 groundwork: bathymetry discovery + `acquire/`

Phase 4.1 says "run empirical attenuation + QC suite at all AOIs." Attempting
it surfaced the fact that the library could not yet *reach* any AOI other than
Sesimbra: a non-Sesimbra site needs a depth grid and at least one clear scene,
and neither was obtainable. This session fixed both, and in doing so found
three defects — all of them silent.

**The EMODnet discovery path was returning nothing, and saying so with HTTP 200.**

`discover_hr_areas` returned `[]` for every region tested, including Sesimbra,
which has four HR datasets. Probing the live service against the Sesimbra bbox:

| Request variant | Result |
|---|---|
| WFS 1.1.0, **latitude-first** (what shipped) | HTTP 200, **0 features** |
| WFS 1.1.0, longitude-first | HTTP 200, 5 features |
| WFS 2.0.0 `typeNames`, longitude-first | HTTP 200, 5 features |
| WFS 1.0.0, bare longitude-first bbox | HTTP 200, 5 features |

WFS 1.1.0 mandates latitude-first axis order for EPSG:4326 and the shipped code
was spec-correct. The server is not. The spec-correct request succeeds with an
empty collection and **nothing raises**, so the caller concludes there is no
high-resolution coverage and falls back to the 115 m DTM — which is precisely
the failure this module exists to prevent. Session 3 measured that fallback: the
coarse grid put Sesimbra's 15 m stratum at ~2.5 m and took depth MAE from
1.77 m to 13.35 m. A silent empty result is therefore not a cosmetic bug; it
silently destroys the retrieval. Now issues **WFS 1.0.0 longitude-first**, with a
lat-first 1.1.0 retry that logs a warning naming axis order.

**The property schema was invented rather than observed.** Live keys are exactly
`download_url`, `edmo_id`, `identifier`, `metadata_url`, `release`, `resolution`.
The code looked for `product_year`/`dtm_year` — missing `release`, the actual
year alias — and for `provider` and `survey_year`, which the service does not
publish at all. `survey_year` is now left `None` rather than parsed out of
`identifier`: the encoding is inconsistent across providers
(`BGS_2011_2_FirthOfLorn`, `201203-Atlantic_Gulf of Cadiz`,
`7mLoughSwillyLoughFoyle`), and a guessed survey year would license a currency
claim the data cannot support.

**The WFS returns one feature per polygon part**, so a multi-part dataset
appears many times — Donegal returned 18 raw features for 4 datasets. Added
`_dedupe` keyed on `(identifier, resolution_arcmin_denom)`.

The root cause of all three is the same and worth stating plainly: the existing
test fixture had been written **from the documentation rather than captured from
the service**, and it contained keys the service never emits. A fixture that
agrees with the docs and disagrees with reality cannot fail. `test_emodnet.py`
now carries `LIVE_FEATURE`, captured verbatim, plus a `TestDiscoveryRequestShape`
class that asserts on the request itself (7 new tests, 33 → **40 passing**).

**Live coverage survey** (deduplicated, after the fix):

| Region | bbox | Datasets | Finest |
|---|---|---|---|
| Sesimbra (baseline) | (-9.15, 38.39, -8.90, 38.51) | 4 | `HR_Lidar_Sul` @ 14.5 m |
| Ireland NW (Donegal) | (-8.6, 54.8, -6.9, 55.5) | 4 | `7mLoughSwillyLoughFoyle` @ **7.2 m** |
| Scotland NW (Summer Isles) | (-5.6, 57.7, -5.0, 58.2) | 3 | `BGS_2005_4_SummerIsles` @ 14.5 m |
| Scotland (Loch Eriboll) | (-4.8, 58.4, -4.3, 58.7) | 1 | `BGS_2010_6_LochEriboll` @ 14.5 m |
| Scotland (Firth of Lorn) | (-6.0, 56.2, -5.3, 56.6) | 5 | all 1/128 @ 14.5 m |
| **Galicia Rías Baixas** | (-9.1, 42.1, -8.6, 42.7) | **0** | — none — |
| **Galicia A Coruña** | (-9.3, 43.0, -8.2, 43.6) | **0** | — none — |
| N Portugal | (-8.95, 41.30, -8.60, 41.95) | 3 | `HR_Lidar_Norte` @ 14.5 m |

**Galicia is dropped.** A geometry-bounds sweep of every feature in
(-9.6, 41.0, -7.5, 44.0) shows the only 1/128 product in that box,
`HR_Lidar_Norte`, spans lon −8.888..−8.546 and lat **40.431..42.061** — the
Portuguese Atlantic coast, its northern tip barely reaching the Minho estuary.
`HR_CaminhaViana` and `HR_PovoaVConde` are likewise Portuguese. Galicia proper
has no HR coverage at any resolution. The plan pre-authorised the outcome
("Drop to 3 AOIs if no tile exists"); the substitution question is in Open.

**`oceanstream/coastal/acquire/` implemented** — the other half of onboarding.
Two modules, deliberately split by the question they answer.

`cdse.py` searches and downloads L1C bundles. Three decisions differ from the
prototype it came from. It uses **OData rather than STAC**, because CDSE's STAC
endpoint does not serve the whole SAFE bundle and ACOLITE wants the bundle. It
searches **by AOI polygon rather than by MGRS tile**: requiring a tile ID is
fine for one known site and useless for onboarding, since nobody knows offhand
which tile covers Loch Eriboll and a coastal AOI frequently straddles two — the
tile now falls out of the result instead of being an input. And **cloud cover
ranks but does not choose**, for the reason below. Downloads mint a fresh token
rather than reusing the search token (a search-time token can be near its
ten-minute expiry; 800 MB is not), write to `.part`, and refuse a transfer under
100 MB — a truncated SAFE fails deep inside an ACOLITE run with an unhelpful
error, so it is worse than no bundle at all.

`planetary.py` answers the expensive question: *which dates are worth
downloading?* A bundle is ~800 MB and an ACOLITE run is tens of minutes, and
tile-level `eo:cloud_cover` cannot answer it, because it describes a
110 × 110 km tile inside which a coastal AOI is a rounding error. So it reads
red and NIR from already-corrected **L2A** in a tight AOI window, masks to
water, and reports median over-water red. L2A here is a **pre-filter only** —
the retrieval itself still runs on L1C through ACOLITE, because Sen2Cor is not
built for water.

**The live run confirms the premise the module was built on.** Screening
June–August 2025 at three sites, 46 overpasses each:

| AOI | clear | marginal | hazy | tile_cloudy | best date | tile cloud | over-water red |
|---|---|---|---|---|---|---|---|
| Sesimbra | 10 | 17 | 10 | 8 | 2025-06-17 | 20.6% | 0.0075 |
| Donegal | 7 | 7 | 5 | 25 | 2025-07-13 | 7.9% | 0.0119 |
| Summer Isles | 14 | 10 | 5 | 14 | 2025-07-13 | 10.3% | 0.0040 |

Donegal 2025-08-27 has **61.3% tile cloud and clear water over the AOI**
(red 0.0177); Summer Isles 2025-08-20 has 50.4% tile cloud and red 0.0068. A
conventional 20–30% cloud filter discards both. This is why over-limit tiles are
recorded as `tile_cloudy` rather than dropped — that limit is a cost control,
not a judgement.

Also worth recording against the Phase 4.2 caveat: the northern AOIs are **not**
hazier than Sesimbra in red. Summer Isles is *clearer* (0.0040 vs 0.0075). The
worry that thresholds tuned on clear Iberian water would reject Atlantic
northern scenes did not materialise, at least in red.

**Two defects found by the new tests, both methodological.**

*The water mask was a relative quantile.* The prototype took the darkest 40% of
NIR in the window. Over an all-water window — the intended case, since the
window is deliberately tight over the site — it still discards 60% of pixels and
keeps the darkest, and since red and NIR both rise with haze and turbidity, this
biases the median **low**, making hazy scenes look clear. Over an all-land
window it reports water anyway. And on a uniform array `nir < quantile(nir, 0.4)`
selects **zero** pixels. Replaced with an absolute cut, `WATER_NIR_MAX = 0.10`,
which is physically grounded: water absorbs NIR almost completely, so clear
water sits near 0.01, turbid coastal water reaches ~0.05, and vegetation and
cloud are above 0.15. Exposed as an argument to raise for a very turbid AOI.

*A neighbouring tile that misses the window raised.* Searching by AOI footprint
routinely returns a tile that does not cover the screening window — a normal
result, not an error — but rasterio raises `RasterioIOError` at *read* time, not
at window construction, so the existing guard missed it. Now checks bounds
overlap explicitly and clamps partial overlaps with `Window.intersection`.

*And one found by the live run.* Where several items share a date, the code took
the lowest cloud cover. At the Summer Isles — which straddles the UTM 29/30
boundary — that chose `T29VPE` with **2,556** water pixels over `T30VUK` with
**53,609**: a clearer tile that barely clips the AOI is the wrong scene, not the
better one. Selection is now coverage-first, cloud-second, computed from STAC
`bbox` metadata at no I/O cost. After the fix `T30VUK` is chosen throughout,
water pixels rise to ~53,000, clear dates go 12 → 14, and the reported tile
cloud for 2025-07-13 becomes the honest 10.3% for the tile that actually
contains the site rather than 3.9% for a sliver.

*And a second found by the next live run — the one that ranks best.* Screening
SW England returned a top-ranked scene at each of two AOIs with a median
over-water red of exactly **-0.1000**. That number is not a measurement, it is
`BOA_ADD_OFFSET / QUANTIFICATION_VALUE`: DN `0` is the Sentinel-2 nodata
sentinel, and the baseline-4.0 offset was being applied to it along with
everything else. An offset gap stops looking like a gap and starts looking like
very dark water — it sits below the NIR cut, so an empty window reads as
wall-to-wall water (71,408 "water" pixels at Mount's Bay) with the darkest red
in the search, and therefore sorts **first**. The scene that would have been
downloaded and pushed through ACOLITE was empty.

The coverage fix above cannot catch this, and the reason is worth keeping: S2
tiles carry wide nodata margins along UTM zone boundaries and orbit edges, and
the **STAC footprint is the tile's bounding rectangle, not its valid-data
extent**. The empty `T30UUA` margin covers Mount's Bay on paper. Only the pixels
know. Masking now happens before the offset; after the fix both bogus scenes
reclassify to `no_water` and the ranking is all `T29UQR`, the tile that holds
the site.

Negatives are deliberately *not* clamped, though clamping would also have hidden
the symptom. Atmospheric correction over dark water legitimately overshoots
below zero — Mount's Bay 2025-08-06 reads -0.0007 over 62,715 valid pixels, and
that is a real, usable scene. Mask the sentinel; keep the physics.

*And a third, from the same family, found by re-screening a corrected window.*
Ranking on reflectance alone rewards the **least observed** scene. A median over
a handful of surviving pixels is measuring a gap in the cloud rather than the
site, and because those gaps are cloud-shadowed they read dark — so they sort
first. At Sesimbra a date with **204** water pixels outranked one with **31,424**
on a window whose clear extent is ~31,500.

`MIN_WATER_PIXELS` cannot catch this either: it is an absolute floor answering
"is this a measurement at all", and it has no idea how much water the window
holds when the view is clear. That number is not knowable up front — but it does
not need to be, because screening a date range reveals it. `best_dates` now
takes the best-observed date in the set as its reference and requires
`min_coverage_fraction` (default 0.5) of it. Fragments are *dropped* rather than
demoted: a 204-pixel window is not a worse download candidate, it contains no
site. After the fix every top pick across the three AOIs is 92–100% observed.

The pattern across all three is worth naming, because it will recur. Each
defect made a *bad* scene rank **best**, and each was invisible to the check
that preceded it — cloud metadata could not see the sliver, footprint coverage
could not see the nodata margin, and a pixel-count floor could not see the
fragment. Every one passed the full unit suite and was exposed only by running
against reality at a site that had not been tried before.

One honest qualification on the third. It was found on the misplaced Sesimbra
window described below, and on the prototype's correct window the coverage floor
changes the ranking not at all — the top four dates are identical with and
without it. The logic is still wrong without the floor, and the guard costs
nothing, but it has not yet been shown to bite on a window that was right.

**A geometry error, and then a worse one correcting it.** Cross-checking the
screening windows against their own AOIs found that Sesimbra's,
`(-9.24, 38.39, -9.15, 38.43)`, sat **entirely outside** its `processing_bbox`
`(-9.15, 38.39, -8.90, 38.51)`. I read that as a stray window and moved it
inside, to `(-9.12, 38.40, -9.00, 38.46)`.

That was backwards. The window was the prototype's `clarity_bbox`, carried over
verbatim and correct. The `processing_bbox` was mine, and it was wrong: I had
taken it from the KelpObserve project documentation, whose AOI 1 is the
**field-campaign strip** along the Arrábida coast east of Sesimbra town. The
prototype's reference retrievals — the ones Phase 4.2's decision gate is
measured against — were produced at the **Cabo Espichel** end. The two boxes
share an edge and nothing else.

The check that settles it is ground truth. All **60** CCMAR diver quadrats sit
in a 111 m × 118 m box at `(-9.2027, 38.4086, -9.2015, 38.4096)`. They fall
inside the prototype's `clarity_bbox`, inside its `habitat_bbox`, and inside its
ACOLITE limit. They fall **outside** the `processing_bbox` I shipped, and
outside my "corrected" window. The AOI I published therefore excluded the only
validation data the site has, and my correction moved the screening window
further from it.

So the numbers that are superseded are the ones from *my* window, not the
prototype's. Re-screened on `clarity_bbox` for the 2026 season, the top four
dates are 2026-08-11, 05-20, 08-28 and 09-07, all at 97.8–100% water coverage.

The prototype's `SiteProfile` now supplies the whole record, with one unit trap
worth writing down: `acolite_limit` is **south, west, north, east**, not the
usual order, so `(38.150, -9.300, 38.550, -8.800)` becomes
`processing_bbox = (-9.30, 38.15, -8.80, 38.55)`. That box deliberately extends
south past the shelf edge to reach optically deep water — which means it is
*not* contained in the fine-bathymetry footprint, and the containment invariant
I had written asserting otherwise would have rejected the correct geometry. The
invariant that earns its keep is the one tied to evidence: the quadrats inside
`habitat_bbox`, and `habitat_bbox` inside the depth grid.

The general lesson is narrower than "check your bboxes". Two documents in this
workspace describe Sesimbra, and they describe different places for different
purposes. Marketing and campaign documentation says where the project works;
the prototype says where the numbers came from. When porting reference values,
only the second is authoritative, and the way to tell them apart is to ask which
one contains the measurements.

**Screening runs on the 2026 season.** Earlier screening in this session used
May–September **2025**, on my assumption that 2026 imagery for the KelpObserve
campaign year was not yet in the archive. It is: the campaigns ran in June and
July 2026 and today is 10 September 2026, so the full season is available at all
three sites. Using 2025 would have put Donegal and the Summer Isles a year away
from the Sesimbra scenes the golden values come from, adding a seasonal and
processing-baseline confound to a comparison whose whole purpose is to separate
water types. The 2025 tables are superseded.

| AOI | overpasses | clear | best four (cloud %, over-water red, coverage) |
|---|---|---|---|
| `sesimbra` | 62 | 18 | 08-11 (2.5, 0.0059, 100%) · 05-20 (0.0, 0.0068, 99%) · 08-28 (11.7, 0.0077, 98%) · 09-07 (8.6, 0.0095, 100%) |
| `donegal` | 63 | 9 | 07-15 (0.4, 0.0061, 97%) · 08-24 (1.9, 0.0071, 100%) · 05-06 (45.1, 0.0080, 84%) · 07-13 (3.4, 0.0080, 93%) |
| `summer_isles` | 66 | 20 | 08-24 (42.1, 0.0001, 92%) · 09-09 (11.9, 0.0030, 94%) · 05-27 (19.0, 0.0038, 94%) · 06-15 (60.5, 0.0054, 89%) |

Two things in that table are worth keeping in view for Phase 4.2. Tile cloud is
decoupled from AOI clarity — Summer Isles 2026-08-24 carries 42% tile cloud and
still gives the cleanest over-water red in the set at 92% coverage, which is why
over-limit tiles are recorded as `tile_cloudy` rather than dropped. And northern
water is **not** turbid here: 0.0001–0.0038 at the Summer Isles is clearer than
Sesimbra's 0.0059–0.0095. If Phase 4.2 finds no usable northern scenes, suspect
`CLEAR_MAX`/`HAZY_MIN`, which were tuned on Iberian water, before concluding
anything about the sites.


onboarding a site meant four manual calls and a hand-written reference record —
against a module docstring that says "onboarding a new AOI should be one call,
not an afternoon." `coastal aoi` now takes `--discover-bathymetry`, with
`--bathymetry-dir` to also fetch and subset to a depth COG. Discovery is opt-in
so the default path never reaches the network, and stopping after discovery is
the default because the archives are hundreds of megabytes and *what covers this
site* is asked far more often than the bytes are wanted. `--seabed-stability`
is an explicit option with **no default**: EMODnet does not publish it, and
asserting it wrongly changes what the product may claim.

```
$ oceanstream process coastal aoi sesimbra --bbox=-9.15,38.39,-8.90,38.51 \
      --discover-bathymetry --seabed-stability=stable_rock
EMODnet HR coverage: 4 dataset(s)
  HR_Lidar_Sul  14.5 m, released 2020
  HR_Sesimbra  28.9 m, released 2018
  HR_Setubal  28.9 m, released 2018
  201203-Atlantic_Gulf of Cadiz  57.9 m, released 2020
selected: HR_Lidar_Sul (not downloaded)
centroid: (-9.025, 38.45)  UTM: EPSG:32629
```

(Transcript kept verbatim. The `--bbox` in it is the campaign strip, not the
shipped Sesimbra AOI — see the geometry note below.)

**Verification state.**

- `pytest oceanstream/tests/unit/coastal`: **686 passed + 2 skipped + 1 xpass**
  across 24 modules (was 573 + 1). Session 6 contributed 113: 7 EMODnet
  request-shape, 23 CDSE, 49 Planetary Computer, 5 AOI-onboarding CLI, 29
  reference-AOI. The two skips are the sites with no diver survey, where there
  is no `habitat_bbox` to check against the depth grid.
- `mypy oceanstream/coastal`: **clean on 34 files**.
- Ruff: only the accepted buckets (`PLR2004` on physics tolerances, etc.).
- Live validation against the real EMODnet WFS and the real Planetary Computer
  STAC at six AOIs, plus a live `coastal aoi --discover-bathymetry` run.

**SW England was surveyed as a fourth-AOI candidate and is not viable on the
EMODnet path.** It is scientifically the most attractive option considered —
*Laminaria ochroleuca* reaches its **northern** range limit there, so it pairs
with Sesimbra as the same species at its cold edge rather than a different
assemblage. Scene supply is also better than expected for the latitude:
Mount's Bay 27 clear of 77 overpasses, Falmouth 25 of 77, Plymouth 14 of 46 —
comparable to the Summer Isles. The bathymetry is the blocker. A geometry sweep
of (-7.0, 49.5, -2.0, 51.5) returns 7 datasets, of which six are Irish
(INFOMAR Celtic Sea/Wexford) or French (`DTM_1-128_Manche`, Cotentin). Exactly
one touches the English coast: `cDTM_SaintMountsBay`, **28.9 m**, lon
-5.731..-5.210, lat 49.756..50.120. Plymouth Sound, Start Bay, north Cornwall,
Lyme Bay and north Devon all return zero. 28.9 m is coarser than Sesimbra's
14.5 m and four times coarser than Donegal's 7.2 m, and Session 3 established
that depth-grid resolution is the dominant error term — so a fourth AOI on a
coarse grid confounds the very comparison Phase 4.2 is meant to make.

This is a limitation of the *one adapter built so far*, not of the data. UKHO
ADMIRALTY and Defra/EA publish far finer open bathymetry for England, and the
`BathymetryReference` model already takes an arbitrary URI — so a
locally-supplied survey grid needs no adapter at all. Both remain open options
if SW England is wanted later.

**The three confirmed AOIs now ship as package data.**
`oceanstream/coastal/aois/{sesimbra,donegal,summer_isles}.json`, each exactly
`AOI.to_dict()` output so `AOI.from_json` round-trips with nothing bespoke in
between; adding a site is adding a file. The loader is deliberately thinner than
`sensors/loader.py` — lazy lookup via `Path(__file__).parent`, no import-time
global registry — because Phase 4 needs to *enumerate* the sites, not to have
them installed into a singleton. This is package data, not a project-relative
default: the bathymetry URIs are absolute EMODnet URLs, asserted by test.

| AOI | processing_bbox | screening window | fine bathymetry |
|---|---|---|---|
| `sesimbra` | -9.30, 38.15, -8.80, 38.55 | -9.24, 38.39, -9.15, 38.43 | `HR_Lidar_Sul` 14.5 m, EDMO 590 |
| `donegal` | -7.70, 55.10, -7.40, 55.30 | -7.65, 55.15, -7.45, 55.30 | `7mLoughSwillyLoughFoyle` **7.2 m**, EDMO 366 |
| `summer_isles` | -5.50, 57.90, -5.20, 58.10 | -5.45, 57.95, -5.25, 58.06 | `BGS_2005_4_SummerIsles` 14.5 m, EDMO 42 |

Sesimbra's row is the prototype's geometry, unchanged. It additionally carries
`habitat_bbox = (-9.212, 38.400, -9.192, 38.420)`, `survey_year = 2011` and
`seabed_stability = "stable_rock"` — the 2011 DGT/APA airborne LiDAR programme
behind the 2020 EMODnet release. Donegal and the Summer Isles carry none of the
three, and the difference is evidential rather than editorial: `habitat_bbox`
marks where benthic claims may be made, and only Sesimbra has a diver survey to
make them from. `survey_year` and `seabed_stability` are set together or not at
all, because EMODnet publishes a product *release* year and no stability — so
knowing either means knowing the source programme, which means knowing both.
They feed straight into what the retrieval may claim about depth error, and
`seabed_stability` alone decides whether depth MAE can carry the accuracy claim.

The tests pin this: the CCMAR quadrat bounds are recorded in the suite and
asserted to lie inside Sesimbra's `habitat_bbox`, `screening_bbox` must sit
inside `processing_bbox`, and any declared `habitat_bbox` must sit inside the
fine-bathymetry footprint. `processing_bbox` is deliberately *not* required to,
since reaching deep water is what it is for.

**Next slice** is Phase 4.1 proper: download the screened scenes to
`.cache/coastal/` on the internal disk (160 GiB free — the earlier plan to stage
on `/Volumes/RP60` is superseded), run ACOLITE, and put the attenuation + QC
suite over each.

### Phase 4.2 — the decision gate, answered

Five scenes, three AOIs, all on **coarse** EMODnet bathymetry (the fine HR
grids cannot supply a deep-water reference; see the defect narrative). The
question the gate asks is whether the 667 nm floor violation and the ~1.0
Lyzenga ratio reproduce everywhere or vary by water type.

**They reproduce everywhere. The cause does not.**

The discriminator turned out not to be the floor violation itself — that is
present in every scene — but the R² ordering between bands that can carry a
bottom signal and bands that physically cannot:

| scene | best carrying | best opaque | ordering | bands trusted |
|---|---|---|---|---|
| sesimbra 06-27 | 0.906 @ 444 nm | 0.344 @ 707 nm | physical | **4 / 4** |
| donegal 07-15 | 0.856 @ 492 nm | **0.947 @ 783 nm** | inverted | 0 / 4 |
| donegal 08-24 | 0.749 @ 492 nm | inverted | inverted | 0 / 4 |
| summer 09-09 | 0.651 @ 665 nm | inverted | inverted | 0 / 4 |
| summer 05-27 | 0.879 @ 560 nm | **0.930 @ 707 nm** | inverted | 0 / 4 |

At both northern sites the single best fit in the entire scene occurs at a
wavelength where pure water extinguishes the bottom signal within about a
metre. That is not a marginal result and it is not a bathymetry-resolution
artefact: Donegal has **more** pixels in the 1–20 m fit window (747,456) than
Sesimbra (411,420). The regression has ample data and is measuring the wrong
thing — a horizontal, depth-correlated gradient rather than vertical
attenuation. Both northern sites are fjordic with river input, so a
nearshore→offshore turbidity gradient and land adjacency are the obvious
candidates, and the two are confounded with depth because depth also increases
offshore.

Three consequences follow, and the third is the one that matters.

1. **The gates work.** Every northern band is rejected; every Sesimbra visible
   band passes. Before Session 6 this table would have been read as four sites'
   worth of k values with no indication that four fifths of it is noise.
2. **Coarse bathymetry was necessary but not sufficient.** It fixed the
   deep-water reference — Donegal's blue moved from k = 0.0026 to a plausible
   0.1245 — and the fits still do not measure attenuation.
3. **Phase 5 has one site's usable evidence, not three.** The gate's question
   *cannot yet be answered*, because two of the three sites do not produce a
   valid measurement to compare against. Answering it needs the horizontal
   gradient handled first: stratify the fit by distance from shore, or port
   `sand_control.py`, whose substrate-control diagnostic exists for exactly
   this failure. That reorders the plan — `sand_control` moves ahead of the
   Phase 5 offset work rather than behind it.

What Sesimbra *does* establish is sharper than before. Red is simultaneously
**trustworthy** (R² = 0.509, every gate passed) and **below the pure-water
floor** (k = 0.010 against a floor of 0.926, a factor of ~90). Statistically
sound and physically impossible at once, which is what excludes "bad fit" as an
explanation for the 667 nm anomaly and leaves an additive residual as the
remaining candidate.

> **Superseded on 2026-09-10.** The additive residual is *not* the candidate.
> See "Phase 5 — ported, and it changed the diagnosis" below. The 667 nm
> anomaly is better explained by the fit window: at 21° pure water extinguishes
> the bottom at 667 nm within ~5 m, while the window runs 1–20 m, so most of the
> regression has no bottom decay to track. R² = 0.509 is the lowest of the four
> bands and should have been read as a warning rather than as a clean bill.

One reporting caveat: the harness scores all nine bands, so it reports "7 bands
below the floor" at Sesimbra where the golden run reports 2. **Resolved
2026-09-10** — see "The floor gates were scoring bands they cannot score".

### Donegal's footprint — the reference fixed, the fit still invalid

The deep-water reference at Donegal was drawn from a box whose deepest pixel was
39.9 m. `processing_bbox` was extended north to 55.55° and ACOLITE re-run; the
mask now reaches 65.8 m on 07-15 and 68.6 m on 08-24. The reference was fixed
exactly as predicted, and the fit did not become valid:

| | deep-mask max | blue k | blue R² | trust |
|---|---|---|---|---|
| 07-15 old | 39.9 m | 0.1245 | 0.588 | `reference_suspect` |
| 07-15 new | **65.8 m** | 0.1761 | 0.677 | cleared |
| 08-24 old | 39.9 m | 0.0380 | 0.748 | `reference_suspect` |
| 08-24 new | **68.6 m** | 0.0286 | 0.379 | **`low_r_squared`** — worse |

The opaque-over-carrying R² inversion is unmoved in all four runs. 08-24
*regressed*, and that is the informative part: **the two extents are in tension.**
A deep-water reference needs a wide extent; the whole-scene attenuation fit
assumes horizontal homogeneity and needs a small one. 19 × 50 km spanning a sea
lough plus 30 km of open Atlantic violates homogeneity harder than the small box
did. The fix is to decouple them — read the reference over the wide extent, fit
attenuation on a local or stratified window — not to shrink back, which would
reinstate a known-invalid reference. The enlarged bbox stays.

### Phase 5 — ported, and it changed the diagnosis

`qc/offset.py` implements `deepwater_additive_offset` (NIR black-pixel
assumption) with per-band values and QA flags; five `QCConfig.offset_*`
thresholds; 16 tests. `resolve_additive_offset` was deliberately **not** ported —
it globs sibling prototype run directories for report JSON, which is pipeline
plumbing rather than science. The prototype's `screen_deepwater` was not ported
either, because `deep_water_pixels` already receives the composite cloud/glint/
foam mask; its lesson survives as the `deep_mask_contaminated` diagnostic rather
than as a silent re-mask.

Two results from running it, both of which revise earlier conclusions.

**1. The empirical k fit is already invariant to a uniform additive offset.**
`deep_water_reference` is the per-band median over deep pixels, and the fit
regresses `band - reference`. Subtract a scalar from `band` and the reference
drops by the same scalar, so the residual is unchanged. RAW and OFFSET-REMOVED
runs are **bit-identical** across all three sites, every band, every R², every
trust flag. The offset is therefore *not* the blocker for `calibrate_bands`. It
still matters for QAA, which uses absolute Rrs; for
`effective_threshold_rhos`, which already took it as a parameter and had no
producer; and for comparing raw spectra across sites.

**2. Sesimbra's "AC residual" is mostly thin cloud.** The new
`deep_mask_contaminated` flag fired on first use: NIR medians 0.0195/0.0218
against a SWIR median of 0.0194 — flat from 830 to 1600 nm. An atmospheric
residual falls with wavelength; cloud is flat. Donegal (NIR 0.0052, SWIR 0.0016,
falling ~3×) is a clean atmospheric residual; Summer Isles (0.0098 / 0.0067) is
borderline. The earlier claim that the residual was 2.6× the green signal at
Sesimbra was measuring cloud.

**Caveat on the Phase 5 acceptance criterion.** The recorded bar was
`k(667) ≥ 0.926 and k(561) ≥ 0.139`. But the prototype's own golden run returns
k(667) = 0.0772 — 0.08× the floor — and so fails it. The criterion states what
physics demands, not what the reference achieves, and must not be used as a
pass/fail bar for the port without that caveat.

### The floor gates were scoring bands they cannot score

Two independent defects, one fix. `pure_water_floor_check` and
`lyzenga_ratio_check` now share `_assessable_bands()`, which excludes a band —
with a stated reason in `skipped_bands` — when either:

- **wl > 700 nm.** `optics.water.a_water` interpolates Pope & Fry 1997, which
  ends at 700 nm, and extrapolates flat above it. Its own docstring says "do not
  use above 700 nm for physics"; the floor check was calling it at every band.
  `kb_pure_water(866)` was built from a_w = 0.624 against a true value near 4.6,
  making the NIR floors ~7× too low — and all but identical (1.297–1.341 from 707
  to 866 nm), which is the giveaway.
- **floor ≥ 1.0 m⁻¹.** Pure water extinguishes the bottom within ~3.5 m against a
  20 m fit window, so the regression has no bottom decay to track and its slope
  is not an attenuation that can be under a floor.

The limits are independent: 690 nm is tabulated but opaque, 707 nm is both.
Excluded bands keep being **fitted**, because `depth_correlated_artefact` detects
a common-mode gradient precisely by noticing that an opaque band outfits a
bottom-carrying one.

| scene | before | after | golden |
|---|---|---|---|
| sesimbra 06-27 | 7 violations | **2** (561, 667), worst 667 at 0.01× | 2, worst 667 at 0.08× |
| donegal 07-15 | 7 violations | **2** (560, 665), worst 665 at 0.08× | — |
| summer 05-27 | 9 violations | **4** (all visible bands) | — |

Sesimbra now reproduces golden's failure *shape* exactly. The residual gap is
our k(667) = 0.010 against golden's 0.077. Donegal's ratio check went from "many
near-unity pairs" to clean once the fabricated NIR pairs stopped being tested —
so that flag had been firing on an artefact of the extrapolation, not on the
data. Summer Isles fails every visible band and is the weakest of the three.

### Session 6 close — what the evidence now supports

Five defects were found and fixed this session, and every one of them was a
**silent** failure: code that returned a plausible number instead of raising.
That is the pattern to keep watching for in this library, because the physics
provides no obvious tell — a k of 0.0026 m⁻¹ looks like a number, not like an
error.

Three claims made earlier in this document were overturned by later measurement
and have been marked superseded rather than deleted:

| Claim | Overturned by |
|---|---|
| The additive residual is the remaining candidate for the 667 nm anomaly | RAW vs OFFSET-REMOVED runs are bit-identical — the fit is differential |
| At Sesimbra the residual is 2.6× the green signal | `deep_mask_contaminated`: NIR ≈ SWIR ≈ 0.019, a flat spectrum, i.e. cloud |
| Donegal shows a common-mode additive artefact (near-unity ratios) | Those pairs only qualified because the extrapolated NIR floor was ~7× too low |

The load-bearing conclusion is unchanged and now better supported: **the gates
work, and only one of three sites currently yields a fit worth trusting.** What
blocks the other two is the horizontal-homogeneity assumption, which is the one
candidate not yet tested.

One evidential gap is worth stating plainly before any of this is written up as
a result: **there is no ground truth at the northern sites.** Sesimbra has 60
CCMAR diver quadrats inside its `habitat_bbox`; Donegal and the Summer Isles have
none. Even a fit that passes every gate there could be shown self-consistent but
not validated.


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
- 3.4 ✅ Outputs: k(λ) JSON, seabed-PAR COG, SDB COG, rho_b COG (labelled
  "effective benthic reflectance", not a material property), cluster COG,
  STAC item carrying the QC verdict. **No z_max COG** — `k(λ)` is scene-uniform
  and ε scalar, so it would be a constant image. Shipped instead as
  `detectability_margin` (z_max − depth) and `detectable` COGs, with the scalar
  `z_max_m` in `detectability.json` and in both rasters' tags.
  Delivered by `coastal/products.py`, `coastal/stac/coastal_emit.py`,
  `coastal/processor.py` and the five CLI subcommands. See Session 5.

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

- [x] 4.1 Run empirical attenuation + QC suite at all AOIs. Five scenes, three
  AOIs (Galicia dropped — zero EMODnet HR coverage; SW England surveyed and
  rejected). All on **coarse** EMODnet bathymetry.
- [x] 4.2 **Decision gate** — answered above. The violation reproduces
  everywhere; the cause does not. Only Sesimbra yields a valid fit, so Phase 5
  has one site's usable evidence rather than three.
- [ ] 4.3 Report z_max distribution per AOI. Coverage and usability are different
  maps. **Blocked on a valid fit at more than one site** — z_max derived from an
  invalid k is a coverage map dressed up as a usability map.

### Phase 5 — Fix the 667 nm additive offset (in the library, evidence-led)

Deliberately AFTER multi-AOI, because multi-AOI data is what distinguishes a
systematic AC-chain bug from a site-specific artefact. **That sequencing paid
off, in the opposite direction to the one intended**: the multi-AOI evidence
showed the offset is not the fault. See "Phase 5 — ported, and it changed the
diagnosis".

- [x] 5.1/5.2 Systematic or site-specific? **Neither.** The offset is real and
  measurable (Donegal 0.0052, Summer Isles 0.0098) but the empirical k fit is
  mathematically invariant to it — `deep_water_reference` subtracts a per-band
  deep-water median before the log, so a uniform offset cancels. RAW and
  OFFSET-REMOVED runs are bit-identical at all three sites.
- [x] 5.3 Separate additive offset from underestimated column term.
  `qc/offset.py` estimates the additive part via the NIR black-pixel
  assumption, with `deep_mask_contaminated` guarding the sample. The joint fit
  across 444/489/561 with 667 as null channel is **not needed for k** (it
  cancels) but remains open for the QAA path, which uses absolute Rrs.
- [ ] 5.4 Acceptance: k(667) ≥ pure-water floor **0.926** *and* k(561) ≥ 0.139
  (`lee.kb_pure_water`, not `a_w`); Lyzenga k(489)/k(667) ≈ 0.042 not 1.0.
  **Caveat added 2026-09-10:** the prototype's own golden run returns
  k(667) = 0.0772, i.e. 0.08× the floor, and therefore fails this bar. The
  criterion states what physics demands, not what the reference achieves. Do
  not use it as a pass/fail gate on the port without saying so.
- [ ] 5.5 **Re-baseline the golden test** — see Verification note.
- [x] 5.6 Detectability ships regardless: z_max from the observed plateau is
  model-free.

**Still open after Phase 5**, and now the leading candidate: the whole-scene fit
assumes horizontal homogeneity. `scene_depth_gradient` fires on every band of
every northern scene and no longer has an offset to blame.

### Phase 6 — CLI + packaging

- 6.1 ✅ `[tool.poetry.extras]` `coastal = [...]`, `coastal-acquire = [...]`,
  both included in `all`. Landed as part of the Phase 1.0 skeleton.
- 6.2 ✅ `coastal_app` Typer group under `process` with all five subcommands
  **implemented** (Session 5; stubbed in Session 1). Wire-up in
  `oceanstream/cli.py`. Conventions applied: kebab-case flags,
  `-o/--output-dir`, `-v`, `--yes`, `--dry-run`. Exit codes: 0 passed,
  1 failed, 2 insufficient data, 3 QC-rejected (only with `--fail-on-reject`).
- 6.3 ✅ Added `"coastal"` to `ProcessingModule` Literal in `providers/base.py`.
- 6.4 `oceanstream/coastal/README.md` — TBD.
- 6.5 Update the three docs carrying the old module name (see Naming section) — TBD.
  **Do not bulk find/replace**: ~191 "benthic" matches across the workspace are
  legitimate science vocabulary, not the old module name.

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
4. ✅ CliRunner integration test: `process coastal detect --aoi … --output-dir …`
   exits 0 and writes a STAC item with a QC verdict. Lives in
   `test_processor.py::TestCommandLine`, driven from a real ACOLITE-shaped
   directory written by the test. Also covers `--fail-on-reject` (exit 3) and
   `--dry-run` (writes nothing).
5. ✅ QC gates fire correctly: assert `pure_water_floor_violated` is True for the
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
- **A failed QC verdict downgrades, it does not abort** (Session 5). Products
  are still written, tagged `diagnostic_only`, and the verdict travels in the
  STAC item. The alternative — refusing to write — hides the evidence needed to
  diagnose the failure.
- **Cluster failure is not run failure** (Session 5). `classify_bottom` raising
  skips one product; it no longer discards the other nine.
- **Colon namespacing in raster tags is forbidden** (Session 5). GDAL reads `:`
  as a metadata-domain separator and silently collapses the tags. Use
  `OCEANSTREAM_*`; keep `oceanstream:*` for JSON and STAC properties only.

## Open

- ✅ **Galicia RESOLVED 2026-09-10 — dropped.** See Session 6. Galicia proper
  (Rías Baixas and A Coruña / Costa Morte) has **zero** EMODnet HR coverage.
  The plan pre-authorised this: "Drop to 3 AOIs if no tile exists," and the
  AOI was listed *(conditional)*. **Open scope question:** substitute northern
  Portugal (`HR_Lidar_Norte`, 14.5 m, 40.43–42.06°N) or run three AOIs? The
  substitute shares Sesimbra's national LiDAR programme, so bathymetry vintage
  and quality are directly comparable — one fewer confound — and it sits under
  the Minho and Douro plumes, a genuinely different optical regime. It does
  **not** carry the BioCost/A Coruña literature link that motivated Galicia.
  Not yet decided by the user.
- ACOLITE packaging: optional dep (user installs) vs containerised (like
  `oceanstream/mohid:24.10`) vs pre-corrected-input-only. Leaning optional dep
  now, container for EDITO.
- Phase 7 option, not committed: register the coastal service as an EarthStudio
  detection provider via `/v1/detect` + HMAC webhook.
- ✅ Golden regression (Verification §2) wired 2026-09-10 in
  `tests/unit/coastal/test_golden_sesimbra.py`. Reproduces the prototype to
  ~1e-8 on all four bands, plus the sensitivity sweep and Lyzenga ratios.
- Still unported from the prototype:
  `sand_control`'s substrate-control diagnostic — now the **next** port, because
  it diagnoses the substrate/depth confound that the horizontal-gradient failure
  is a form of — and
  `reef_calibration.compare_with_qaa` (empirical reef k vs offshore QAA k — a
  direct measure of the water-mass mismatch that no other diagnostic
  isolates). Also unported and lower value: `bottom_signal_by_depth`.
  `resolve_additive_offset` and `screen_deepwater` were deliberately declined —
  see Phase 5 above. `deepwater_additive_offset` **has now been ported**.
- **Open defects, in priority order** (2026-09-10):
  1. *The whole-scene fit assumes horizontal homogeneity.* The leading blocker.
     `scene_depth_gradient` fires on every band at both northern sites. Read the
     deep-water reference over a wide extent; fit attenuation on a local or
     stratified window.
  2. *`min_deep_reference_pixels` counts the wrong thing.* It counts pixels, not
     depths, so a reference drawn entirely from shallow water passes. Test the
     depth *distribution* — e.g. require z·Kd ≥ 6 for some minimum count.
  3. *Reprojection coverage warns rather than fails.* "covered only 13.2% of the
     target grid" is a log line. Sesimbra/HR at 0.4% failed only by luck, via a
     downstream `ValueError`.
  4. *Reprojection silently clips the DTM to the scene footprint.* Worked around
     per-site at Donegal by widening `processing_bbox`; no general guard. This is
     what made defect 2 invisible.
  5. *Selection bias is self-concealing.* `fit_band_attenuation` requires
     `residual > 0`, so a contaminated reference discards most pixels and fits a
     positively-biased remnant. Caught only indirectly, via `reference_suspect`.
- The additive offset has **no producer wired into the pipeline yet**.
  `qc/ac_uncertainty.effective_threshold_rhos` has always accepted it as a
  parameter and has never been given a value; `qc/offset.py` now computes one.
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
