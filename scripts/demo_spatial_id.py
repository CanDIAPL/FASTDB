#!/usr/bin/env python3
"""
Demo: Spatial ID Multi-Precision Querying

This script demonstrates how the spatial_id UUID enables querying objects
at different precision levels with hierarchical filtering.

UUID Layout (128 bits):
    high64: [Reserved:2][HEALPix:62]
    low64:  [MJD_ms:43][ProcVer:16][DataRelease:5]

Field ordering enables hierarchical filtering:
    1. Position (HEALPix) - Filter by sky location first
    2. Time (MJD) - Then narrow by observation time
    3. ProcVer - Then by processing version
    4. DataRelease - Finally by data release

Key Features Demonstrated:
1. Same sky position → same spatial_group (across ALL time/procver/dr)
2. Different precision levels via bit masking
3. Hierarchical filtering: position → time → procver → dr
4. Multi-master safe deterministic generation
"""

import sys
sys.path.insert(0, 'src')

from spatial_id import (
    generate_spatial_id,
    extract_all,
    extract_healpix,
    spatial_group_int,
    same_spatial_group,
    healpix_to_radec,
    extract_approx_radec,
    NSIDE,
    MJD_EPOCH,
)


def demo_spatial_grouping():
    """
    Demonstrate that objects at the same position share grouping prefix
    regardless of observation time or data release.
    """
    print("=" * 70)
    print("DEMO 1: Spatial Grouping Across Time/Data Releases")
    print("=" * 70)
    print()

    # Same position, different times and data releases
    ra, dec = 180.000102, 45.008  # Sky position
    procver = 1  # Same processing version

    observations = [
        (60000.0, 0, "Realtime observation 1"),
        (60001.0, 0, "Realtime observation 2 (1 day later)"),
        (60100.0, 0, "Realtime observation 3 (100 days later)"),
        (60000.0, 1, "DR1 processed"),
        (60100.0, 2, "DR2 processed"),
    ]

    print(f"Sky Position: RA={ra}°, Dec={dec}°")
    print(f"Processing Version: {procver}")
    print()

    spatial_ids = []
    for mjd, dr, desc in observations:
        sid = generate_spatial_id(ra, dec, mjd, procver, dr)
        spatial_ids.append(sid)
        group = spatial_group_int(sid)
        print(f"  {desc}")
        print(f"    MJD: {mjd}, DataRelease: {dr}")
        print(f"    spatial_id: {sid}")
        print(f"    group_int: {group}")
        print()

    # All should have the same spatial group
    groups = [spatial_group_int(sid) for sid in spatial_ids]
    all_same = len(set(groups)) == 1
    print(f"All observations in same spatial group: {all_same}")
    print()


def demo_precision_levels():
    """
    Demonstrate querying at different precision levels by masking bits.
    """
    print("=" * 70)
    print("DEMO 2: Multi-Precision Querying via Bit Masking")
    print("=" * 70)
    print()

    # Generate spatial_id for a reference position
    ra_ref, dec_ref = 180.00012, 45.002
    sid_ref = generate_spatial_id(ra_ref, dec_ref, 60000.0, 1, 0)
    healpix_ref = extract_healpix(sid_ref)

    print(f"Reference Position: RA={ra_ref}°, Dec={dec_ref}°")
    print(f"HEALPix index: {healpix_ref}")
    print()

    # Different precision levels by masking HEALPix bits
    precision_levels = [
        (62, "Full precision (~0.0004\", native NSIDE=2^29)"),
        (48, "~0.05\" resolution (group matching)"),
        (40, "~0.8\" resolution"),
        (32, "~13\" resolution"),
        (24, "~3.5' resolution"),
        (16, "~1° resolution"),
    ]

    print("Precision Levels via Bit Masking:")
    print("-" * 50)
    for bits, desc in precision_levels:
        mask = ((1 << bits) - 1) << (62 - bits)
        masked = healpix_ref & mask
        print(f"  {bits} bits: {desc}")
        print(f"    Mask: 0x{mask:016X}")
        print(f"    Masked HEALPix: {masked}")
        print()

    # Example: Find nearby objects at different precision
    print("Example Query Patterns:")
    print("-" * 50)
    print("""
    -- Full precision match (exact position, ~0.0004")
    SELECT * FROM diaobject
    WHERE spatial_group(rootid) = spatial_group('{sid_ref}')

    -- Coarse match (~13" radius)
    SELECT * FROM diaobject
    WHERE (spatial_group(rootid) >> 16) = (spatial_group('{sid_ref}') >> 16)

    -- Very coarse match (~1° radius)
    SELECT * FROM diaobject
    WHERE (spatial_group(rootid) >> 48) = (spatial_group('{sid_ref}') >> 48)
    """.format(sid_ref=sid_ref))


def demo_hierarchical_filtering():
    """
    Demonstrate hierarchical filtering: position → time → procver → dr.
    """
    print("=" * 70)
    print("DEMO 3: Hierarchical Filtering (Position → Time → ProcVer → DR)")
    print("=" * 70)
    print()

    ra, dec = 180.0, 45.0

    print(f"Same Position: RA={ra}°, Dec={dec}°")
    print()

    # Different times, procvers, and data releases at same position
    observations = [
        (60000.0, 1, 0, "Realtime, AP v1"),
        (60000.0, 2, 0, "Realtime, AP v2"),
        (60100.0, 1, 0, "100 days later, AP v1"),
        (60100.0, 2, 1, "100 days later, AP v2, DR1"),
    ]

    print("Observations at same position (different time/procver/dr):")
    print("-" * 50)
    for mjd, procver, dr, desc in observations:
        sid = generate_spatial_id(ra, dec, mjd, procver, dr)
        group = spatial_group_int(sid)
        print(f"  {desc}")
        print(f"    spatial_id: {sid}")
        print(f"    group_int: {group}")
        print()

    # Check that all have same spatial group
    sids = [generate_spatial_id(ra, dec, mjd, pv, dr) for mjd, pv, dr, _ in observations]
    all_same_group = len(set(spatial_group_int(s) for s in sids)) == 1
    print(f"All observations in same spatial group: {all_same_group}")
    print("(Expected: True - spatial grouping is position-only)")
    print()
    print("To filter by time/procver/dr, compare the full spatial_id or extract fields.")
    print()


def demo_coordinate_recovery():
    """
    Demonstrate recovering coordinates from spatial_id.
    """
    print("=" * 70)
    print("DEMO 4: Coordinate Recovery from spatial_id")
    print("=" * 70)
    print()

    test_positions = [
        (0.0, 0.0, "Equator at RA=0"),
        (180.0, 45.0, "Northern hemisphere"),
        (270.0, -45.0, "Southern hemisphere"),
        (123.456789, 67.890123, "High precision input"),
    ]

    print("Coordinate Recovery Precision:")
    print("-" * 50)
    for ra_in, dec_in, desc in test_positions:
        sid = generate_spatial_id(ra_in, dec_in, 60000.0, 1, 0)
        ra_out, dec_out = extract_approx_radec(sid)

        ra_err = abs(ra_out - ra_in) * 3600  # arcseconds
        dec_err = abs(dec_out - dec_in) * 3600  # arcseconds

        print(f"  {desc}:")
        print(f"    Input:  RA={ra_in:12.6f}°, Dec={dec_in:12.6f}°")
        print(f"    Output: RA={ra_out:12.6f}°, Dec={dec_out:12.6f}°")
        print(f"    Error:  RA={ra_err:.6f}\", Dec={dec_err:.6f}\"")
        print()


def demo_multi_master_determinism():
    """
    Demonstrate that the same inputs always produce the same spatial_id.
    """
    print("=" * 70)
    print("DEMO 5: Multi-Master Determinism")
    print("=" * 70)
    print()

    print("Generating spatial_id 5 times with identical inputs:")
    print("-" * 50)

    args = (180.0, 45.0, 60000.0, 1, 0)
    results = [generate_spatial_id(*args) for _ in range(5)]

    for i, sid in enumerate(results, 1):
        print(f"  Generation {i}: {sid}")

    all_equal = len(set(results)) == 1
    print()
    print(f"All generated spatial_ids identical: {all_equal}")
    print("(This ensures conflict-free multi-master replication)")
    print()


def demo_sql_integration():
    """
    Show example SQL queries using spatial_group function.
    """
    print("=" * 70)
    print("DEMO 6: SQL Query Integration")
    print("=" * 70)
    print()

    sid = generate_spatial_id(180.0, 45.0, 60000.0, 1, 0)
    group = spatial_group_int(sid)

    print("Example SQL Queries:")
    print("-" * 50)
    print(f"""
-- Find all objects in the same spatial group as a target
SELECT diaobjectid, ra, dec
FROM diaobject
WHERE spatial_group(rootid) = {group};

-- Find all observations of objects in a spatial region
SELECT s.diaobjectid, s.midpointtai, s.psflux
FROM diasource s
JOIN diaobject o ON s.diaobjectid = o.diaobjectid
WHERE spatial_group(o.rootid) = spatial_group('{sid}'::uuid);

-- Count objects by spatial group
SELECT spatial_group(rootid) as grp, count(*) as cnt
FROM diaobject
GROUP BY 1
ORDER BY cnt DESC
LIMIT 10;

-- Find nearby groups (within ~13")
SELECT DISTINCT spatial_group(rootid)
FROM diaobject
WHERE (spatial_group(rootid) >> 16) = ({group} >> 16);
    """)


def main():
    print()
    print("SPATIAL_ID DEMO: Multi-Precision Querying for Multi-Master Replication")
    print("=" * 70)
    print()
    print(f"Configuration: NSIDE = 2^29 = {NSIDE:,}")
    print(f"Native resolution: ~0.0004\" (0.4 milliarcseconds)")
    print(f"MJD epoch: {MJD_EPOCH}")
    print()

    demo_spatial_grouping()
    demo_precision_levels()
    demo_hierarchical_filtering()
    demo_coordinate_recovery()
    demo_multi_master_determinism()
    demo_sql_integration()

    print("=" * 70)
    print("Demo Complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
