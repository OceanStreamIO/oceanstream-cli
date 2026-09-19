# Optical validation: practical options

## Release scope and the supplied inputs

The satellite scenes, fine/coarse bathymetry and existing Sesimbra polygons are
already supplied. They are enough to run a diagnostic benchmark. Missing optical
observations do not prevent processing or distribution of experimental software.

A research-software beta can publish reproducible products, quality flags,
withheld-depth comparisons and known limitations. Attenuation, detectability,
effective bottom reflectance and seabed PAR remain experimental, without validated
site-specific accuracy claims. Withheld depths from the calibration bathymetry
measure agreement with that source, conditional on its errors and datum; they do
not establish independent absolute SDB accuracy.

The original scientific-beta target is stronger: accepted, independently validated
physical products on two dates at both Sesimbra and Donegal. That gate remains in
place in the benchmark tool. A completed diagnostic run cannot satisfy it.

## Start with a Sesimbra pilot

The practical first option is to explore borrowed equipment and optical expertise
through the existing CCMAR collaboration. No equipment availability, partnership,
booking or expenditure is assumed. A new campaign validates new coincident
satellite acquisitions; it cannot recreate the optical conditions of the six
historical benchmark scenes.

An initial planning design is two successful satellite-overpass survey days,
weather contingency, and roughly 8–12 stations spanning depth, water and substrate
conditions, including an offshore reference. This is a logistics starting point,
not a statistically established minimum. Repeat stations, prioritize a few near
the overpass, and measure temporal variability. Stations visited later are not
automatically simultaneous satellite matchups.

| Measurement | Practical equipment or method | What it can test |
|---|---|---|
| Independent depth and water level | Georeferenced soundings or surveyed depths, with acquisition-time datum correction | SDB and inversion-depth errors |
| Simultaneous near-bottom and subsurface PAR | Two calibrated underwater PAR sensors with synchronized logging | Fraction of subsurface PAR reaching the bottom |
| Spectral light through the water column | Calibrated spectral radiometry profiles | Downwelling attenuation and QAA/PAR assumptions |
| Water-leaving spectral reflectance | Calibrated above-water or near-surface radiometry | Atmospheric-corrected satellite input reflectance |
| Bottom spectra and substrate contrast | Calibrated underwater spectra, georeferenced photos and transects | Effective bottom reflectance and contrast assumptions |

Ordinary downwelling attenuation **Kd is not the empirical effective two-way k**.
Validating the latter needs a compatible contrast-versus-depth experiment over
independently characterized bottom patches, with held-out surface observations.
PAR sensors alone do not validate every optical product.

Match survey footprints to the relevant native Sentinel-2 band support and spatial
variability. A small diver quadrat alone does not describe a satellite pixel.
Define the light reference plane: kelp-canopy-top light and under-canopy light are
different quantities. The library's PAR product is a fraction, not absolute
irradiance; absolute irradiance needs a surface-light input.

Use the calibration, deployment and quality-control methods in the
[IOCCG in situ radiometry protocols](https://ioccg.org/what-we-do/ioccg-publications/ioccg-protocols/).
Bottom measurements also require documented illumination/viewing geometry and
measurement uncertainty; the [IOCCG benthic reflectance working group](https://ioccg.org/group/benthic/)
addresses these issues.

## Two feasible levels

1. **Depth and PAR pilot:** independent depth, paired PAR sensors, existing sonde
   casts and photos. This can support depth/PAR assessment; the other optical
   quantities remain experimental.
2. **Full optical campaign:** add spectral water-column, water-leaving and bottom
   measurements plus contrast transects. Plan this with an optical specialist,
   then extend to Donegal if the pilot demonstrates useful matchups.

Before pricing a campaign, establish equipment loans/calibration, boat and field
staff availability, optical analysis support and weather contingency. A useful
initial discussion brief is: “Could paired underwater PAR measurements and
calibrated spectral radiometry be added to two satellite-coincident Sesimbra survey
days, and what equipment and expertise could be borrowed?” This document does not
send that request.

## Deliverables and alternatives

Retain raw observations, UTC times, locations, depth/datum, sensor calibration and
serial numbers, deployment geometry, weather/wave notes, replicates and uncertainty
budgets. Follow [SeaBASS radiometry submission requirements](https://seabass.gsfc.nasa.gov/wiki/data_submission_special_requirements)
for supporting measurement/calibration records. Convert qualified observations to
the [benchmark evidence format](../../benchmarks/coastal-beta/README.md#evidence-csv)
only after checking quantity, units, independence and spatial/temporal matching.

Public archives such as [SeaBASS](https://seabass.gsfc.nasa.gov/) may offer component
validation at other locations. No suitable two-site matchup dataset has been
identified here. Neither an unrelated archive matchup nor a satellite-derived
reference map establishes validation at Sesimbra and Donegal. Field collection
can remain a later effort while the research software's limitations are explicit.
