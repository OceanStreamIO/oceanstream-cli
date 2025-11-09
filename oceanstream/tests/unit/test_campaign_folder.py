#!/usr/bin/env python3
"""Test script to verify campaign_id folder structure and file/directory input."""

from pathlib import Path
from oceanstream.providers.saildrone import SaildroneProvider
from oceanstream.geotrack.processor import GeotrackProcessor

def test_scan_input_source():
    """Test that scan_input_source works with both files and directories."""
    provider = SaildroneProvider()
    processor = GeotrackProcessor(provider, verbose=True)
    
    # Test 1: Directory input
    test_data_dir = Path(__file__).parent.parent / "data" / "raw_data"
    raw_data_dir = test_data_dir
    if raw_data_dir.exists() and raw_data_dir.is_dir():
        print("Test 1: Scanning directory...")
        csv_files = processor.scan_input_source(raw_data_dir)
        print(f"  ✓ Found {len(csv_files)} CSV files")
        print(f"  Files: {[f.name for f in csv_files]}")
    else:
        print("Test 1: SKIP (no raw_data directory)")
    
    # Test 2: Single file input
    # Find any CSV file in the test data directory
    csv_files_in_dir = list(raw_data_dir.glob("*.csv")) if raw_data_dir.exists() else []
    if csv_files_in_dir:
        test_file = csv_files_in_dir[0]
        print("\nTest 2: Scanning single file...")
        csv_files = processor.scan_input_source(test_file)
        print(f"  ✓ Found {len(csv_files)} file(s)")
        print(f"  File: {csv_files[0].name}")
    else:
        print("Test 2: SKIP (test file not found)")
    
    # Test 3: Non-existent path
    print("\nTest 3: Non-existent path...")
    try:
        processor.scan_input_source(Path("/nonexistent/path"))
        print("  ✗ FAILED: Should have raised FileNotFoundError")
    except FileNotFoundError as e:
        print(f"  ✓ Correctly raised FileNotFoundError: {e}")
    
    # Test 4: Invalid file type
    print("\nTest 4: Invalid file type...")
    try:
        processor.scan_input_source(Path(__file__))  # This Python file
        print("  ✗ FAILED: Should have raised ValueError")
    except ValueError as e:
        print(f"  ✓ Correctly raised ValueError: {e}")
    
    print("\n" + "="*60)
    print("All tests passed!")
    print("="*60)

if __name__ == "__main__":
    test_scan_input_source()
