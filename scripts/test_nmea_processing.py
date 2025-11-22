#!/usr/bin/env python3
"""Test script for NMEA raw data processing.

This script demonstrates how to process NMEA raw data files
and convert them to CSV format suitable for GeoParquet ingestion.

Now uses the actual NMEA processor module instead of duplicated code.
"""

from pathlib import Path
import sys

# Add parent directory to path to import oceanstream modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from oceanstream.sensors.processors.nmea_gnss import process_nmea_raw


def main():
    """Process NMEA raw data file."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Process NMEA raw data")
    parser.add_argument(
        "--sampling-interval",
        type=float,
        default=None,
        help="Sampling interval in seconds (e.g., 1.0 = 1 Hz, 10.0 = 0.1 Hz)",
    )
    args = parser.parse_args()
    
    input_file = Path("raw_data/r2r/RR2401_gnss_gp170_aft-2024-02-17.txt")
    output_dir = Path("out/nmea_test")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Use different filename based on sampling
    if args.sampling_interval:
        output_file = output_dir / f"gnss_navigation_{args.sampling_interval}s.csv"
    else:
        output_file = output_dir / "gnss_navigation.csv"

    print(f"Input file:       {input_file}")
    print(f"Output file:      {output_file}")
    if args.sampling_interval:
        print(f"Sampling:         1 point per {args.sampling_interval}s")
    print()

    # Process the file using the actual module
    stats = process_nmea_raw(input_file, output_file, sampling_interval=args.sampling_interval)

    print("\n" + "=" * 60)
    print("PROCESSING COMPLETE")
    print("=" * 60)
    print(f"Lines read:        {stats['lines_read']:,}")
    print(f"Lines parsed:      {stats['lines_parsed']:,}")
    if args.sampling_interval:
        print(f"Points merged:     {stats['data_points_merged']:,}")
        print(f"Points written:    {stats['data_points_written']:,}")
        print(f"Decimation ratio:  {stats['decimation_ratio']*100:.1f}%")
    else:
        print(f"Data points:       {stats['data_points_written']:,}")
    print(f"Output file:       {output_file}")
    print(f"Output size:       {output_file.stat().st_size:,} bytes")

    # Show sample of output
    print("\n" + "=" * 60)
    print("SAMPLE OUTPUT (first 5 rows)")
    print("=" * 60)
    with open(output_file, "r") as f:
        for i, line in enumerate(f):
            if i >= 6:  # Header + 5 rows
                break
            print(line.rstrip())

    print("\nNow you can process this CSV with oceanstream:")
    print(f"  oceanstream process --provider r2r geotrack convert \\")
    print(f"    --input-source {output_file} \\")
    print(f"    --output-dir out/geoparquet \\")
    print(f"    --campaign-id RR2401_GNSS_TEST")


if __name__ == "__main__":
    main()
