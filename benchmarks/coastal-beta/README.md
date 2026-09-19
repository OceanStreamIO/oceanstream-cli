# Scientific beta benchmark protocol

**The full scientific release remains blocked.** A diagnostic benchmark can run
with the supplied inputs and explicit missing-input flags. Freeze the regions,
settings, observations, and evaluation split before comparing retrievals. Use at
least two dates at Sesimbra and two at Donegal, with Summer Isles failures retained.
Do not relax checks to make Donegal pass. See [practical validation options](../../docs/coastal/field-validation.md)
for the distinction between a research-software beta and validated physical products.

## Published Key West reference

[key-west-v2/experiment.json](key-west-v2/experiment.json) registers a separate
Sentinel-2 SDB component reconstruction using public NOAA lidar, a spatial MLLW
datum correction, two atmospheric-correction recipes and buffered held-out blocks.
Both runs completed. See [results and reproduction commands](../../docs/coastal/key-west-reference.md).
This component experiment has its own input lock and runner; it is not a full
five-product scientific acceptance run.

The follow-up [key-west-optics-v1 registration](key-west-optics-v1/experiment.json)
holds lidar-plus-gauge depth fixed to test attenuation and bottom reflectance.
Both expanded ACOLITE variants completed with separate offshore references and
local calibration regions. Their outputs remain diagnostic: regional attenuation
and QAA plausibility checks fail. See the [optical results, limitations and
reproduction commands](../../docs/coastal/key-west-optics.md). Its own input lock
preserves the earlier SDB experiment unchanged.

The [key-west-replication-v1 registration](key-west-replication-v1/experiment.json)
adds fresh adjacent evaluation strips, sealed fits and comparisons with nine and
10,000 calibration observations. All 24 models completed. The original chart
points remain unavailable, so the nine-observation lidar proxy is labelled
explicitly. See [results and remaining replication gaps](../../docs/coastal/key-west-replication.md).

## Supplied six-scene baseline

[supplied-v3/benchmark.json](supplied-v3/benchmark.json) connects the existing local
scenes, separate fine/coarse bathymetry, Sesimbra deep-water patches and reporting
rectangle. Local calibration uses the existing site screening bounds. Northern
reference polygons come from coarse depths >50 m outside the reporting rectangles,
clipped to the available processing domains. All scene optical-depth and cloud
screens still apply. Existing dates and newly registered regions remain exploratory.

To reproduce registration on this workspace (choose a new output directory):

```bash
PYTHONPATH=. python scripts/coastal/register_supplied_benchmark.py \
  --kelp-root /Users/andrei/kelp_observe \
  --output benchmarks/coastal-beta/supplied-v4
python -m oceanstream.coastal.benchmark freeze \
  benchmarks/coastal-beta/supplied-v4/benchmark.json \
  benchmarks/coastal-beta/supplied-v4/benchmark-v4.lock.json
python -m oceanstream.coastal.benchmark run \
  benchmarks/coastal-beta/supplied-v4/benchmark-v4.lock.json \
  out/coastal-beta/supplied-v4
```

The registration script locates sd-data-ingest from its own location; `--kelp-root`
locates the other checkout. No input data are downloaded or duplicated. Small
region files and complete configurations are retained beside the registration.
The lock contains absolute local paths and content checksums; another workspace
must recreate its own registration against the same archived inputs and code.

`purpose: diagnostic` requires each scene's exact recorded `acolite_version` and
unconditionally prevents `evidence_complete`. This permits inspecting the supplied
caches without pretending their differing build strings establish one historical
ACOLITE code revision. Scientific registrations still require one global version.
Neither mode fabricates tide corrections or measured uncertainty. Missing fields,
assumed sensitivity parameters and provisional regions remain rejection flags.

The runner prints scene progress, keeps coverage diagnostics on insufficient-data
failures, and releases each scene's arrays before loading the next. Outputs and
failure reports are isolated under `out/coastal-beta/.../runs/`. Exit code **2** means
the scientific evidence gate is incomplete; inspect each scene's `success` and
`status` to distinguish completed diagnostic retrievals from execution failures.

The current baseline is **v3**. Earlier runs are retained: v1 exposed sampling
before reference-polygon masking; v2 confirmed the sampling repair and exposed a
Summer Isles geometry issue after projection. V3 normalizes generated northern
polygon coordinates to 1e-9 degrees (sub-millimetre precision) and preflights all
polygons in each scene CRS. These geometry repairs do not alter optical thresholds.
See [results and remaining problems](../../docs/coastal/six-scene-benchmark.md).

## Register and execute

1. Copy `benchmark.example.json`. Supply real paths, one pinned ACOLITE version, local AOIs with registered calibration/deep-water polygons, independent fine/coarse depth rasters, tide corrections, and uncertainty configuration. Existing satellite runs have already been inspected: any new regions derived from those results are exploratory until tested on a new held-out date.
2. Declare independent observation datasets and their measurement methods. The optional evidence CSV format is below. Verify independence and datum/units before registration.
3. Freeze to a **new** lock file, then run it:

```bash
python -m oceanstream.coastal.benchmark freeze benchmark.json benchmark-v1.lock.json
python -m oceanstream.coastal.benchmark run benchmark-v1.lock.json out/benchmark-v1
```

Freeze refuses missing files and overwriting an existing lock. It hashes scene rasters/settings, AOIs, depth/reference/region files, tide/configuration, evidence, and coastal source code. Run verifies the hashes and recorded ACOLITE version, records each failed scene, and writes `progress.json` and `benchmark.json` in an isolated directory. Changing code or inputs requires another registration. Keep the lock in version control; archive source data and software environments beside the report. Changing an old lock is not a new preregistration.

## Evidence CSV

Required columns:

```text
scene_id,region,product,band_nm,longitude,latitude,reference,reference_sigma,depth_m,unit,partition,source_cell_id
```

- `scene_id` and `region` refer to frozen scene and calibration-region IDs. Coordinates are WGS84. `reference_sigma` is the reference observation's standard uncertainty, not a retrieval uncertainty. The runner samples retrieval values and uncertainty from actual outputs.
- `partition` is `calibration` or `evaluation`. All observations must carry stable source-cell IDs. Sharing a source cell between partitions rejects that evidence; repeated pixels from a coarse cell are not independent observations.
- Evidence metadata must declare `independent: true`, `source`, and `method`. Set `used_for_calibration: true` if the dataset also supplied calibration depths. Such SDB points must land in the processor's held-out partition; optical evidence used for calibration is rejected as independent validation. These declarations are auditable claims, not proof of independence; the scientific reviewer must check them.

| `product` | Reference quantity | `unit` |
|---|---|---|
| `attenuation` | Effective two-way k for the specified band and local region; ordinary downwelling Kd alone is not this quantity | `m^-1` |
| `detectability` | Held-out observed substrate contrast in surface reflectance at the measured depth and band, consistent with the declared endmembers | `rhos` |
| `sdb_depth` | Independent instantaneous positive-down depth, or a properly withheld native source depth | `m` |
| `rho_b` | Independently characterized effective bottom reflectance corresponding to the inversion convention and band | `1` |
| `seabed_par` | Simultaneous bottom PAR divided by subsurface PAR, with spectral/temporal correspondence documented | `1` |

Spectral products require `band_nm`. Scalar/raster products still require the region and depth fields so errors can be stratified. Nodata predictions count against coverage and never become zero observations. The report publishes sample counts, bias, MAE, RMSE, reference/retrieval uncertainty summaries, accepted coverage, and rejection reasons by site/date/region/band/depth. Current statistical errors are conditional; there is no claim that these summaries are calibrated confidence intervals.

## Release review

The automatic gate requires evidence for every requested physical product, at both release sites and at least two dates, with measured uncertainty and accepted coverage. It also requires the Summer Isles stress test to remain present. `evidence_complete` only means those inputs and scores exist. `release_ready` remains false pending review of the actual errors and operating limits; the code does not authorize publication on its own.

A review must establish independent optical matchups, representative spatial/depth coverage and sufficient independent samples, matching quantities/units, calibrated uncertainty, and documented failure conditions. Bathymetry/diver quadrats do not validate attenuation, reflectance, or PAR. Satellite-derived PAR maps are not independent optical reference measurements. Record practical operating limits from the evidence; do not invent a universal accuracy threshold or use shared calibration data as validation.

The missing field evidence is a separate dependency for the full physical-product
release. It does not prevent this diagnostic benchmark. A
[Sesimbra pilot and staged field campaign](../../docs/coastal/field-validation.md)
can begin with independent depths and paired PAR measurements, adding spectral
radiometry and bottom optical observations when equipment and expertise are available.
No suitable two-site optical evidence was supplied with this implementation.
