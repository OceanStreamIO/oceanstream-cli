# Next echosounder datasets after RR2205

Surveyed on 2026-10-22: R2R cruise RR2402, the Celtic Explorer data in NCEI's water-column bucket, and openly available European ICES surveys of two weeks or more recorded with EK80. The baseline is [RR2205](../rr2205/mn5-cruise-run.md): 264.5 GB, 32 days, five CW channels pinging in turn with complex samples, CCHDO CTD casts, and raw data streamed from NCEI's public bucket.

## Summary

| | RR2402 | Celtic Explorer in NCEI | IESSNS 2021 (Iceland) |
|---|---|---|---|
| Survey | CCE-LTER process cruise P2402, California Current | Mid-Atlantic mapping transits, not ICES surveys | ICES IESSNS, Icelandic part, R/V Árni Friðriksson |
| Dates | 2024-02-17 to 03-18, 31 days | 2016, 7 and 8 days | 2021-07-05 to 07-26, 22 days |
| Echosounder | EK80, five channels, CW with about 11 FM sessions | EK60 (four channels) | EK80, five channels, CW 1.024 ms |
| Raw size | 737 GB, 4,685 files | 25.6 GB and 2.9 GB | about 1.02 TB, 2,114 files |
| Open access | No. R2R data request only; not in NCEI | Yes, NCEI public bucket | Yes, PANGAEA, CC-BY-4.0, served from tape |
| CTD | 99 casts as raw SBE 911+ and an R2R product; no CCHDO | Marine Institute ERDDAP, CC-BY-4.0 | 54 casts made, none found openly |
| Fit with the current pipeline | Needs FM separation and per-mode grouping | Needs the EK60 path | Needs the power/angle path and retuned ping windows |

Recommendation:
- **IESSNS 2021 is the ICES EK80 survey to process next.** The same Icelandic survey is on PANGAEA for 2020 (30 days, 640 GB) and 2022 (18 days, 497 GB), with the same ship and channels, so the three years form a 70-day series.
- **Request RR2402 from R2R now** so it is available later. R2R says the request may need the chief scientist's clearance.
- **Drop Celtic Explorer as an ICES source.** The Marine Institute's acoustic surveys are not in NCEI.

## RR2402

- **Cruise.** R/V Roger Revelle, San Diego to San Diego, 2024-02-17 to 03-19, chief scientist Michael Stukel. It is the first cruise of CCE-LTER Phase IV (NSF OCE-2224726) and not GO-SHIP. It ran five Lagrangian cycles of 3–5 days, following drifting sediment-trap and incubation arrays between 31.5 and 35.6 N. It also included a shelf transect from Point Arguello to Cambria, one Santa Barbara Basin cast and CalCOFI station 93.30. Depths range from about 50 m on the shelf to about 4,000 m at cycle 3.
- **Availability.** R2R fileset 160207 holds the EK80 data: 4,685 `.raw` files, 737 GB, released 2024-07-12. NCEI's public bucket stops at RR2214, and R2R's download link for this fileset returns 404. The only route is a request at `https://www.rvdata.us/distro_request/RR2402`.
- **Recording.** Almost continuous from 02-17 to 03-18, at about 23.8 GB a day, against 8.3 GB a day for RR2205. Files after 02-21 are named `RR2402_-D…-T….raw`. The event log records:
  - three modes: "Mission CW", "Normal CW", and about 11 FM sessions of 1.5–5 h, mostly around local noon
  - cross-talk and noise in both CW modes
  - an EK80 crash on 02-21/22, and the EK80 found off for 5.4 h on 03-10
- **Configuration.** Not read, because no raw bytes are public. The shipment's calibration files from the RR2401 sonar calibration cruise (2024-02-12) cover 18 kHz CW and 38–200 kHz in both CW and FM. That suggests the same five transducers as RR2205.
- **CTD.** 99 casts as raw SBE 911+ files (`.hex`, `.XMLCON`), plus R2R's processed product at NCEI accession 0297186. There is no CCHDO entry. The SBE 911+ adapter on branch `ctd-casts-on-main` (`casts_from_sbe911`) is the route to sound speed and absorption, once checked against real casts.
- **Differences from RR2205:**
  1. CW and FM files must be split before Sv.
  2. Grouping must follow the recording mode. Keeping the channel set with the most pings, as on RR2205, could silently drop whole blocks.
  3. Re-check cross-talk pruning; it is turned off for RR2205.
  4. Days hold 2–6 times more pings, so a whole-day task may not fit in 144 GB.
  5. Shelf days put the seabed in range.
  6. Read the transducer depth from the configuration rather than assuming 6.2 m.
- **Estimate.** About 1,500–3,000 billing-hours, which is 0.3–0.6 % of the allocation.

## Celtic Explorer in NCEI

- **What NCEI holds.** One folder, `data/raw/Celtic_Explorer/CE15-ATLANTIC-CROSSING`, with 58 GB of EM302 water-column data and no split-beam data. Two more Celtic Explorer cruises are filed under `Celtic_Voyager/`:

  | Cruise | Route | Dates | EK60 | Multibeam |
  |---|---|---|---|---|
  | CE16007 | Galway → St John's | April 2016, 7 days | 25.6 GB | EM302 |
  | CE16014 | TRASNA, St John's → Galway | May 2016, 8 days | 2.9 GB | EM302 / EM2040 |

  NCEI also has Zarr versions of both cruises in `s3://noaa-wcsd-zarr-pds/level_1/Celtic_Voyager/`.
- **EK60 configuration (CE16007).**
  - Four GPT channels (18, 38, 120, 200 kHz), pinging together.
  - Pulse 1.024 ms.
  - Transducer depth 8.8 m.
- **ICES surveys.** None of these cruises is one. The Marine Institute's acoustic surveys are not in NCEI and are available on request only (`datarequests@marine.ie`):
  - CSHAS, October
  - WESPAS, June–July
  - the blue whiting survey
  
  The 2020 CSHAS record still names an EK60. The Marine Institute's CTD data are open, CC-BY-4.0, at `https://erddap.marine.ie/erddap/tabledap/IMI_CTD`.

## European ICES EK80 surveys with open raw data

The only open raw EK80 data found from an ICES survey of two weeks or more is the Icelandic part of IESSNS. The Marine and Freshwater Research Institute publishes it on PANGAEA under CC-BY-4.0:

| Year | PANGAEA | Days | Files | Raw size | EK80 software |
|---|---|---|---|---|---|
| 2020 | 985386 | 30 | 6,424 | about 640 GB | 1.12.4 |
| 2021 | 975782 | 22 | 2,114 | about 1.02 TB | 2.0.0 |
| 2022 | 985485 | 18 | 4,987 | about 497 GB | 21.15.1 |

**Access.** Anonymous HTTPS at `https://download.pangaea.de/dataset/<id>/files/<name>`. The first request for a file returns 503 while it loads from tape, and the file is served 2–7 minutes later. Each dataset's tab export (`?format=textfile`) lists every file with its size and MD5. Each year also has a calibration XML, and the trawl data are separate PANGAEA datasets.

**Differences from RR2205 (2021):**
1. **Power and angle samples, not complex.** Sv uses the CW power path.
2. **All five channels ping together, about every 0.9 s**, against about 39 s per channel on RR2205. That is roughly 40 times more pings per channel. Ping-count windows in the presets need scaling by ping interval, and a day is about 46 GB of raw data, so day tasks will likely need splitting.
3. **Range is about 500 m.** The seabed is in range on the shelf.
4. **The files carry no transducer depth.** PANGAEA gives the transducer face at 9 m, which must be set by hand.
5. **The environment XML holds defaults** (10 °C, 35 PSU), and no CTD was found openly. Sound speed and absorption could come from a Copernicus Marine physics reanalysis along the track, in the same form as the CTD casts.
6. **One calibration XML is published, identical for 2020–2022, and it differs slightly from the values in the files** (38 kHz gain 27.03 vs 27.05 dB, beam angle −20.30 vs −20.7 dB). We must pick one and record which.
7. **Format details differ by year.** 2022 adds RAW4 datagrams, 2020 changes transmit power mid-survey, and file names carry prefixes such as `A2021_12-D…`.
8. **Our transfer tool needs a new source type.** `xfer.py` streams from S3, not from an HTTPS source that answers 503 first.

**Not usable:**
- Iceland 2018 and 2019 are EK60 and 7 days each.
- The Norwegian Marine Data Centre server holds only short FM test excerpts.
- Ifremer, Marine Scotland, Wageningen, DTU Aqua, IEO, IPMA and SLU have no open raw archives found.
- The ICES acoustic database holds processed NASC only.

## Other quick options

Four smaller Revelle EK80 cruises are already in NCEI's public bucket: RR2201 (22 GB), RR2207 (47 GB), RR2209 (18 GB) and RR2212 (24 GB). They are probably close to RR2205's setup, so they could run with today's driver while the larger datasets are prepared. Their configuration has not been checked yet.

## Sources

- R2R API: `https://service.rvdata.us/api/{cruise,fileset,product,file_manifest}/cruise_id/RR2402`. R2R cruise page: https://www.rvdata.us/search/cruise/RR2402
- NCEI water-column bucket `s3://noaa-wcsd-pds/data/raw/`, and the NCEI metadata for CE16007, CE16014 and CE15-Atlantic-Crossing (DOIs 10.25921/8qcf-0s09, 10.25921/y3ty-e274, 10.25921/at8d-yf38).
- PANGAEA: https://doi.pangaea.de/10.1594/PANGAEA.975782, .985386, .985485
- MFRI survey report HV 2022-06: https://download.pangaea.de/reference/112493/attachments/IESSNS_IS_2021.pdf
- Marine Institute: https://www.marine.ie/site-area/areas-activity/fisheries-ecosystems/acoustic-surveys, https://erddap.marine.ie/erddap/info/IMI_CTD/index.html
- CCE-LTER P2402 cruise report: https://ccelter.ucsd.edu/wp-content/uploads/2025/02/P2402_Cruise_report-2.pdf (not read; the site refused connections from EDITO).
