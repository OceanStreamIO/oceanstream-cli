# Sesimbra Habitat Benchmark v1: Kelp Percentage Cover

## 1. Objective and scope

Build a reproducible experiment answering:

> How well can Sentinel-2 and Pléiades Neo predict measured kelp percentage cover at Sesimbra, and does water-column correction improve that prediction?

Deliver quantitative comparisons and exploratory cover maps. Success means reliable evidence, including a negative result if prediction is poor.

Agreed scope:

- Percentage cover is the primary target; presence/absence remains supporting information.
- Compare both sensors on a common 10 m grid, with a separate native 1.2 m PNeo experiment.
- Include simple regression models and an optical-correction comparison.
- Retain conflicting field metadata pending review.
- Treat September evaluation as exploratory unless its independence is established.
- Exclude new field campaigns, Clay retraining, species classification, biomass estimates, deployment and scientific release certification.

The current quadrats support evaluation at sampled locations. They do not establish complete-pixel cover or unbiased habitat-area estimates. The distinction between field sampling and image support is material in submerged vegetation mapping. [Relevant cover-mapping research](https://www.sciencedirect.com/science/article/pii/S0034425708001259)

Statistical support is pilot-scale. All 90 events lie within about 120 × 140 m, and neighbouring transects are 25–53 m apart. The 30 July quadrats occupy 18 distinct 10 m cells. Depth stratum carries most July cover variation (shallow mean about 70%, deep about 26%), so the depth-only model is the comparator that spectral models must beat. A depth-only or null result is a likely and valid outcome.

## 2. Pre-registration findings

Checks on 2026-09-19 read source files without modification and fitted no model. None related imagery to cover. They changed the protocol as follows.

| Finding | Protocol consequence |
|---|---|
| No registered deep-water patch intersects the PNeo scene (0 of 8; all ≥100 m deep, ≥3 km offshore). The scene contains water to about 113 m. | PNeo-specific reference region, registered from geometry and depth only (§4). |
| Lidar depth is relative to LAT. No acquisition-time water levels were supplied. | Predicted water levels are a required input for the corrected branch (§4). |
| The library's Sentinel-2 `blue` role is 444 nm (B1, 60 m native); the PNeo `blue` role is 490 nm. | Predictor bands are defined by wavelength (§4). |
| July dates conflict: workbook 6–7 July, current GeoPackage 7 July, earlier GeoPackage 7–8 July with dive times. July deep stratum is 16 m (workbook) or 17 m (GeoPackage). September: 2 vs 1 September; 6/15 m vs 7/17 m. | Reconciliation covers both campaigns (§3). |
| The earlier timed GeoPackage records tape positions with Q1 at 20 m, surveyed first. Workbook quadrat order is unconfirmed. | Order confirmation and a reversed-order sensitivity scenario (§3, §4). |
| The September workbook recodes June–July Juvenile/Adult from per-quadrat counts to per-plant flags. Juvenile and adult totals each differ for 2 of 60 quadrats. Cover values and event counts are unchanged. | Reported as a source revision (§3). |
| September deep T1 Q1–Q4 (labelled 15 or 17 m) lie on 5.2–7.8 m of lidar depth. Median label-minus-lidar residuals in every other stratum are 1–4 m. Four September shallow quadrats (T3 Q1–Q3, Q5) lack lidar. | Depth-consistency QC and larger displacement scenarios (§4). |
| Same-label quadrats move 21–102 m (median 47 m) between July and September and share 2 cells. | September is described as a repositioned, later-season test at the same site (§5). |
| At the quadrats, reflectance differences between depth strata on both PNeo dates are similar across bands and present in NIR, which cannot see a 7–17 m bottom. Rayleigh-corrected NIR is about 0.018 in July. One date is near-specular: July at about 6° or September at about 19°, depending on the viewing-azimuth convention. | Glint and NIR-residual diagnostics (§4, §5). |
| July PNeo is `REFLECTANCE` with `ATMOSPHERIC_RADIOMETRIC_SETTING=RAYLEIGH`: Rayleigh-corrected, not TOA. September is `BASIC`. ACOLITE inverts both to TOA. | Conversion test (§6). |
| The Sentinel-2 L2A catalogue lists candidates in every survey window, including S2B on 3 September within about a minute of the PNeo acquisition. 9 July produced no ACOLITE output in an earlier batch. | Scene-ranking rule and cross-sensor check (§4). |
| September cover is concentrated near zero: 11 of 19 scored quadrats are 5%, plus 10 surveyed absences. The July mean is 48%. | Signed bias is reported alongside MAE. Recorded as prior inspection (§3, §5). |

## 3. Reference dataset and experiment registration

Create a new, versioned reference dataset from the September delivery, which contains all three campaigns. Compare its June–July records with earlier files and report revisions rather than appending duplicate observations.

- Preserve all **90 survey events**, identifying campaign, transect, shallow/deep stratum and quadrat.
- Retain original dates, depth labels, coordinates, species, cover values and source row identifiers.
- Use explicit `EMPTY` records as surveyed absence and derive zero kelp cover with that provenance. Keep the kelp-positive quadrat with missing cover as positive occurrence but unavailable for cover regression.
- Aggregate individual-plant rows within each quadrat without counting plants as independent samples or summing repeated quadrat-cover measurements.
- Use an explicit taxon mapping; do not inherit the older importer’s broad keyword definition of kelp. The delivery contains only *Saccorhiza polyschides* and `EMPTY`, so the registered target is *Saccorhiza* percentage cover.
- Report source revisions, including the June–July Juvenile/Adult recoding.
- Retain sonde measurements for contextual QC. Exclude unresolved sonde timestamps from acquisition matchups.

**Reconciliation**

Record date, depth and quadrat-order reconciliation separately, for **July and September**. Send CCMAR one query covering both campaigns' survey dates, the depth label of each stratum, quadrat numbering relative to tape position, and the positions of September deep transect T1.

Classify every join until CCMAR responds:

- `confirmed`: confirmed by CCMAR.
- `reconciled_by_rule`: the campaign, stratum, transect and quadrat mapping is one-to-one; only label values differ; the event passes depth-consistency QC (§4). Usable in registered comparisons, with the basis stated in every result.
- `provisional`: an ambiguous mapping or a failed depth-consistency check. Exploratory analyses only, never definitive validation.

Until confirmed, assume the workbook numbers quadrats in survey order (Q1 at tape 20 m), as the timed July GeoPackage records. Carry every plausible survey date into matchup windows: June {2 June}; July {6, 7, 8 July}; September {1, 2 September}.

**Prior use**

Audit September references in processing outputs, model artifacts and experiment records. Absence of a local record does not prove an untouched test set; keep `prior_use_unknown` until confirmed. The registration records known prior use:

- 2026-08-30: `kelp_observe/tools/lee_demo/pneo_vs_c2.py` correlated July PNeo brightness and red-edge NDVI with July cover (combined brightness r = −0.30, n = 30). It labels the July product TOA; it is Rayleigh-corrected.
- June–July Sentinel-2 per-quadrat diagnostics under `kelp_observe/outputs/`.
- 2026-09-19 plan review: summarised September cover values and stratum means, and sampled PNeo reflectance at September quadrats by stratum. It did not relate imagery to cover for any campaign.
- A 2026-09-19 search found no September field data or September PNeo order in processing outputs or model artifacts of kelp_observe, sd-data-ingest, kelp-observe-classifier, kelpobserve-app or seaweed_watch.

**Registration**

Register the protocol before model fitting: inputs, source revisions, join classes, candidate models, imagery-selection rules, spatial groups, metrics, exclusions and every numeric threshold in this plan. Commit the coastal module, runner and registration before freezing, so locks record a commit as well as file hashes. Preserve the existing 60-event dataset and earlier benchmark locks.

## 4. Imagery, spatial support and models

**Imagery preparation**

- Use the delivered PNeo acquisitions from **8 July (11:38 UTC) and 3 September 2026 (11:34 UTC)**.
- Discover Sentinel-2 acquisitions within four days of the survey dates. Require that window to hold across all plausible dates; otherwise mark the matchup unavailable. For July, the window is 4–10 July.
- Rank scenes that pass the registered image-quality checks by:
  1. smallest maximum offset from the plausible survey dates;
  2. smallest time difference from the same campaign's PNeo acquisition;
  3. valid-water coverage over the habitat AOI;
  4. acquisition identifier.

  Never choose scenes using cover-prediction errors. Known candidates (catalogue query 2026-09-19): 30 May, 31 May, 2 June (two), 5 June; 5, 9, 10 July; 29 August, 31 August (two), 3 September, 5 September. Under this rule, September selects 3 September if it passes QC.
- Process both sensors with one pinned ACOLITE revision (currently a clean checkout at `64a02ff`, `20260421.0-86`) and recorded, sensor-specific settings.
- Handle July PNeo `REFLECTANCE` and September `BASIC` through their respective conversions. ACOLITE converts the Rayleigh-corrected July product back to TOA using the DIMAP reflectance and radiance gains and biases.
- Verify reflectance units, band identity, cloud/glint masks and alignment using stable land features. Identical grid metadata is insufficient.
- Compute and record the sun-glint angle for every acquisition, resolving the viewing-azimuth convention against ACOLITE's geometry. PNeo has no SWIR. NIR deglinting is acceptable at quadrat depths, where the NIR bottom signal is negligible, but not over bright shallow sand. Report the NIR residual per sample as a QC diagnostic, never as a predictor.
- Compare PNeo aggregated to 10 m with Sentinel-2 for the 3 September pair, over water in the habitat AOI and over stable land features. This uses no field targets, so it may run before registration. It reports agreement by band and tunes neither correction.
- Obtain predicted water levels relative to LAT for every acquisition and recorded survey time from Instituto Hidrográfico predictions for Sesimbra. Record source, station and datum.
- Use the existing Sesimbra habitat AOI, intersected with valid imagery and surveyed bathymetry coverage. All 90 quadrats lie inside it, and 3.6 of its 3.9 km² lie inside the PNeo scene.
- Use the EMODnet HR `590_HR_Lidar_Sul` bathymetry (2011 survey, about 11 × 14 m, LAT). Preserve its resolution and datum. Assign 10 m cells the area-weighted mean of intersecting lidar cells, and native PNeo samples their containing lidar cell. Never interpolate to a finer apparent resolution. Events without lidar depth are missing for depth-dependent models, never imputed.

**Spatial support**

- Anchor the common EPSG:32629 10 m grid to the selected July Sentinel-2 scene. Aggregate PNeo reflectance by valid-area averaging with fractional pixel overlap, since 10 m is not a multiple of 1.2 m. Require at least 80% valid coverage per comparison cell.
- For multiple quadrats in one cell and campaign, report their sampled mean cover, count and variability. Label this explicitly as a sampled mean, not full-cell truth.
- Retain individual events for the native PNeo experiment and supporting diagnostics.
- **Depth-consistency QC.** For each event, compute the recorded stratum depth minus the containing lidar cell's depth plus the predicted water level at dive time. Where no dive time is recorded, use the day's predicted range. Flag events outside the registered tolerance: proposed 3 m, covering lidar vertical error, whole-metre labels and within-cell relief; confirm it at registration. Exclude flagged events from registered comparisons and retain them in labelled sensitivity analyses. Report whether a neighbouring lidar cell reconciles each flag.
- Evaluate fixed 2, 5, 10 and 20 m coordinate-displacement scenarios in eight directions, plus a reversed quadrat-order scenario. These measure sensitivity, not calibrated positional uncertainty.
- Use native multispectral PNeo data for quantitative features; exclude pansharpened imagery.

**Registered model comparison**

Run the same fixed candidate families for each sensor and spatial scale:

1. Training-set mean cover.
2. Depth-only ridge regression.
3. Blue/green/red reflectance ridge regression.
4. Blue/green/red plus bathymetry ridge regression.
5. A small random forest using those same spectral and depth predictors.

Define predictor bands by wavelength, never by library role: Sentinel-2 B2/B3/B4 (about 490/560/665 nm) and PNeo B/G/R (about 490/560/655 nm). The Sentinel-2 444 nm band is excluded from predictors.

Standardize ridge inputs using training data only; use `alpha=1`. Fix the forest at 300 trees, maximum depth 3, minimum leaf size 5 and seed 42. Avoid hyperparameter searches with this small dataset. With about 12 training cells per fold, the minimum leaf size allows at most two leaves; report the forest as fitted.

**Optical-correction comparison**

Add ridge and forest variants using the library’s QAA/Lee effective-bottom-reflectance predictors at the same three bands, plus surveyed bathymetry. QAA may use all bands for scene-mean IOPs over reference water.

- Reuse existing coastal preparation, inversion and QC functions.
- Invert with lidar depth plus the predicted water level at acquisition. Effective bottom reflectance scales with exp(k_b·H), so an uncorrected water level biases it differently on each date. Without verified water levels the corrected branch is diagnostic only and excluded from the July-to-September comparison.
- **Sentinel-2:** use the existing registered offshore-reference polygons clipped to each scene. The six-scene benchmark flagged `deep_mask_contaminated` at Sesimbra on both June dates; retain such failures.
- **PNeo:** no existing polygon intersects the scene. Register a sensor-specific reference region from geometry and depth only, without inspecting reflectance within it: EMODnet DTM depth ≥50 m, ≥1 km from land and from the habitat AOI, and ≥100 m inside the valid footprint on both dates. About 0.30 km² qualifies, in three parts. It supplements the Sentinel-2 patches and does not replace them.
- Insufficient reference coverage or failed reference QC makes the corrected branch unavailable for that sensor and date. Do not select replacement patches based on model performance.
- QAA v6 coefficients are approximated at PNeo band centres; keep the library's flag.
- Preserve missing-tide, reference-quality, physical-plausibility and experimental-PNeo flags.
- Finite outputs failing physical QC may be evaluated only as a separately labelled diagnostic comparison. Missing outputs remain missing.
- Compare corrected and uncorrected models on identical observations as well as their individual coverage. Predictive improvement does not certify physical accuracy.

## 5. Execution, evaluation and deliverables

Implement an isolated experimental workflow in `sd-data-ingest`, following the existing component-benchmark pattern:

`inventory → prepare → freeze → fit → evaluate → report`

Use a dedicated runner at `scripts/coastal/run_sesimbra_habitat_benchmark.py`, with registration under `benchmarks/sesimbra-habitat-v1/`. Reuse coastal library functions without changing the existing five-product acceptance gate or production classifier defaults.

Interfaces:

- Registration JSON: source locations, reconciliation decisions and join classes, CCMAR query status, scenes and their ranking, processing settings, water-level sources, numeric thresholds and fixed experimental choices.
- Reference GeoPackage and sample tables: event provenance, cover, missingness, spatial support, depth-consistency result and eligibility.
- Immutable locks and fit manifests: hashes of source data, processed imagery, code (with its commit), settings, splits and fitted parameters.
- Separate fitting and evaluation commands. September targets live in a separate file that only the evaluation command opens.

Evaluation design:

- Use **July alone for the primary matched-sensor development comparison**. The July sensors are at least a day apart, so sea state, water level and glint differ between them.
- Perform leave-one-transect-out spatial evaluation, keeping both depth strata together. Remove training samples sharing the held-out imagery cell or extraction footprint. Folds are neighbouring transects, not independent sites; report each held-out unit's distance to the nearest training unit.
- Report insufficient folds explicitly. A fold is insufficient when fewer than 8 training units, or fewer than 3 in either depth stratum, remain.
- Fit the registered candidates on July, seal their parameters, then evaluate September without retuning. Describe September as a repositioned, later-season test at the same site, neither a repeat-position test nor geographically independent.
- Use June as a separate Sentinel-2 supplementary experiment; do not silently give Sentinel-2 extra training data in the primary comparison.
- Report all candidates. Do not select a production winner using September results.

Metrics:

- MAE, RMSE and signed bias in **percentage points**. Report signed bias alongside MAE for September, where cover is concentrated near zero.
- Counts and usable coverage by sensor, campaign, transect, depth stratum, join class and observed-cover range.
- Paired differences on common observations, including each candidate against depth-only.
- Both raw and bounded-to-0–100 predictions, with clipping frequency.
- Missing predictions and exclusion reasons; no substitution with zero.
- Per-transect results and dispersion. With only three transects, do not present quadrat-level confidence intervals as evidence of broad spatial independence.
- Glint angle and NIR residual by sample and stratum.

Deliver:

- Reconciled/provisional reference inventory, join classes, depth-consistency table, CCMAR query log and unresolved-issues table.
- Prior-use record.
- Frozen protocol, inputs and model artifacts.
- The 3 September cross-sensor radiometric check.
- Numerical results and a readable benchmark report.
- Exploratory cover rasters, validity/extrapolation masks and QC layers for each candidate.
- Separate native-PNeo maps, explicitly distinguished from the common-grid comparison.
- A concrete account of additional reference evidence needed for pixel-cover or habitat-area validation.

## 6. Tests and completion criteria

Test the failure modes that could invalidate the experiment:

- Repeated plant rows do not multiply survey samples or cover.
- Explicit absence survives blank cover; positive missing cover never becomes zero.
- Source revisions, duplicate identifiers and ambiguous joins are exposed.
- Date parsing preserves the July, September and sonde discrepancies.
- BASIC/REFLECTANCE conversion, band mapping, masks and nodata handling remain consistent. The REFLECTANCE-to-TOA conversion reproduces the DIMAP formulae on sampled pixels.
- Predictors select bands by wavelength; the Sentinel-2 444 nm band cannot enter a reflectance model.
- Scene ranking is deterministic under date uncertainty and ties.
- Depth-consistency QC flags a synthetic displaced event and passes an undisplaced one.
- Grid aggregation preserves valid-area accounting under fractional pixel overlap and prevents repeated cells from inflating sample counts.
- Training and evaluation groups do not overlap; all preprocessing fits use training data only. The fit command cannot open the September target file.
- Changed inputs or fitted parameters invalidate their locks.
- Optical failures remain visible and cannot improve apparent performance by silently dropping difficult observations.
- A synthetic end-to-end run reproduces metrics, preserves failed runs and produces completion manifests only after required outputs finish.

V1 is complete when the registered comparisons and maps are reproducible, or unavailable branches have explicit evidence-backed reasons, and the report states exactly which conclusions the supplied data support. Unresolved metadata and weak cover predictability are valid findings; neither is grounds for weakening the protocol.

## 7. Sequencing

Before registration, with no model fitting:

1. Commit the coastal module, runner scaffolding and registration directory.
2. Send the CCMAR query and obtain water-level predictions.
3. Download the June and September Sentinel-2 L1C candidates. Run ACOLITE on every candidate scene and both PNeo dates.
4. Build the reference dataset, join classes and depth-consistency QC, with their tests.
5. Register the PNeo reference region and run the 3 September cross-sensor check.

Then register and freeze. Fit, evaluate and report last.
