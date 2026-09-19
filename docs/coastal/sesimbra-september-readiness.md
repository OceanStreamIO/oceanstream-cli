# Sesimbra September inputs: readiness audit

Inspected 2026-09-19. This is an input inventory and compatibility review, not a
habitat accuracy assessment or a frozen experiment registration. Source files
were read without modification. No model was fitted or evaluated.

## Field observations

The source directory is
`/Users/andrei/kelp_observe/data/ccmar-diving/sept/`.

- `TNA_Aquaserv_KelpData2026.xlsx`, sheet `Folha1`, contains 260 observation
  rows across June, July and September. Multiple rows describe individual kelp
  within a quadrat; they are not independent labelled map samples.
- September occupies rows 213–261: 49 records representing 30 quadrats,
  keyed by date, depth stratum, transect and quadrat number. Twenty quadrats
  contain a kelp species record and ten are explicitly `EMPTY`.
- Nineteen September quadrats have a numeric percentage-cover entry. The ten
  `EMPTY` records have blank percentage cover; one kelp-positive quadrat also
  lacks percentage cover (`K222`, 15 m, transect 2, quadrat 3). Preserve explicit
  absence separately from unknown cover. A generic blank-to-zero conversion
  would turn that positive quadrat into a false absence.
- `Transects kelp AQUASERV Jun-Sep 2026.gpkg`, layer `transects`, contains
  90 point features, 30 per campaign. September has three transects with five
  quadrats at each of two labelled depth strata.
- `Maps kelp AQUASERV Jun-Sep 2026.gpkg`, layer `maps`, contains three
  MULTILINESTRING features labelled June, July and September. These are line
  geometries; they do not by themselves provide labelled habitat polygons.
- The sonde workbook includes 211 September profile rows: 146 under
  `Arrábida_Sep26_17m` and 65 under `Arrábida_Sep26_6m`. This inventory does not
  establish profile quality or optical-model validation.

## Metadata discrepancies requiring resolution

| Source | September date | Depth labels |
|---|---|---|
| Kelp workbook | 2026-09-02 | 6 m and 15 m |
| Transect GeoPackage | `01/set` | 7 m and 17 m |
| September mapping line | Name includes `01/09/26, 15:33:37` | Not established |
| Sonde workbook | Underlying Excel value 46062 = 2026-02-09 | Site names say September, 6 m and 17 m |

The sonde discrepancy is present in the original XLSX XML, not introduced by
extraction. It is consistent with a day/month interpretation problem, but its
correction is not confirmed. June sonde date values also need review. Retain raw
dates, depth labels and source identifiers until an explicit reconciliation is
recorded. Differences in depth labels are not acquisition-time tide measurements.

## Pléiades Neo delivery

Archives are under `/Users/andrei/kelp_observe/data/pneo/`.

| Property | July | September |
|---|---|---|
| Archive prefix | `WO_000598128_1_2` | `WO_000618890_1_2` |
| Acquisition date/time from DIM metadata | 2026-07-08 11:38:15.6 | 2026-09-03 11:33:59.9 |
| Processing level | ORTHO | ORTHO |
| Multispectral product | MS-FS, six bands | MS-FS, six bands |
| Radiometric processing | REFLECTANCE | BASIC |
| Delivered multispectral grid | EPSG:32629, 1.2 m, 4920 × 2817 | Same |

Both contain red, green, blue, NIR, red-edge and deep-blue bands, plus a separate
panchromatic product. Identical grid metadata does not establish subpixel
coregistration, habitat classification resolution or bathymetric visibility.

All 90 supplied quadrat coordinates fall inside both image footprints. All 30
September coordinates have nonzero, unmasked RGB samples in both deliveries.
This establishes raster availability at the points, not usable seabed signal:
cloud, glint, water clarity, positional errors and habitat mixing remain unchecked.

September is one day after the workbook survey date, or two days after the
GeoPackage date. July is one to two days after the corresponding workbook survey
dates. These are promising temporal pairings, subject to confirming field dates.

The two delivery types require their appropriate radiometric conversions before
comparison. The local ACOLITE implementation has separate BASIC and REFLECTANCE
conversion branches in `acolite/pleiades/l1_convert.py`. Reprocess under a pinned,
consistent configuration and verify units and image alignment before treating
July–September differences as ecological change. Supplier REFLECTANCE metadata
alone does not establish water-surface atmospheric correction.

## Consequences for the habitat benchmark

1. Build a versioned three-campaign reference table using explicit survey-event
   keys and a documented date/depth crosswalk. Retain all 90 events, raw source
   labels, kelp presence, cover and missingness separately; audit earlier
   campaign rows for revisions rather than appending the whole new workbook.
2. Update the field importer before using September. The current
   `tools/lee_demo/merge_quadrat_cover.py` reads older inputs, recognizes only
   June/July, and drops rows with blank cover before preserving `EMPTY` records.
   Its current logic would exclude the new September absences and the unscored
   kelp-positive quadrat. Do not overwrite the earlier 60-event reference.
3. Prepare comparable July and September PNeo multispectral imagery and matched
   Sentinel-2 observations; check native sensor support and geolocation before
   extracting habitat predictors. PNeo imagery is an input or interpretation aid,
   not independent ecological ground truth for a model using that imagery.
4. Subject to prior-use review, reserve September for temporal confirmation of
   candidates selected using earlier data. Inventorying labels is not a model
   evaluation, but the future protocol must record any earlier model selection
   or inspection using September. A new date on repeated transects does not
   provide a geographically independent map test.
5. Assess spatial support, non-kelp coverage and habitat boundaries before
   expanding the study area or requesting more surveys. Ten new absence
   quadrats are useful; their count must not be equated with ten independent
   satellite pixels or representative absence coverage across Sesimbra.

The coastal physical-product release gate remains unchanged. The next milestone
is a habitat-specific benchmark with explicit spatial and temporal evaluation,
not automatic acceptance of optical products or habitat predictions.
