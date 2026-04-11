# Nortek Signature AD2CP

Acoustic Doppler Current Profiler with integrated echosounder capability.

## Models

- Signature 100 (100 kHz, long-range profiling)
- Signature 250 (250 kHz, medium-range)
- Signature 500 (500 kHz, medium-range, common for ocean observatories)
- Signature 1000 (1000 kHz, short-range, high resolution)

## File Format

Binary `.ad2cp` format. Parsed by `oceanstream.adcp.ad2cp_reader.read_ad2cp()`.

## Output Variables

| Variable | Unit | Description |
|----------|------|-------------|
| echo_amplitude | counts | Raw acoustic backscatter amplitude |
| Sv | dB re 1 m⁻¹ | Volume backscattering strength (derived) |
| velocity | m/s | Current velocity (beam or earth coordinates) |
| sound_speed | m/s | Speed of sound in water |
| temperature | °C | Water temperature (from instrument sensor) |
| pressure | dbar | Water pressure |
| heading | ° | Instrument heading |
| pitch | ° | Instrument pitch |
| roll | ° | Instrument roll |

## Deployments

- OceanLab Munkholmen (SINTEF Ocean / NTNU) — fixed buoy, 80 m depth
