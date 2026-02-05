#!/usr/bin/env python3
"""
Demo: Spatial ID for Multi-Master Conflict-Free Grouping

This script demonstrates the practical workflow for using spatial_id:
1. Converting RA/Dec coordinates to a spatial_id UUID
2. How spatial_id enables position-based queries without lookup tables
3. Comparison of old rootid queries vs new spatial_id queries
"""

import sys
sys.path.insert(0, 'src')

from spatial_id import (
    generate_spatial_id,
    extract_all,
    extract_healpix,
    extract_mjd,
    extract_procver,
    extract_data_release,
    spatial_group_int,
    healpix_to_radec,
    extract_approx_radec,
    NSIDE,
    MJD_EPOCH,
)


def demo_coordinate_to_uuid():
    """
    Show the complete workflow: RA/Dec → HEALPix → spatial_id UUID
    """
    print("=" * 70)
    print("STEP 1: Converting Coordinates to spatial_id UUID")
    print("=" * 70)
    print()

    # Input parameters (what you have from an observation)
    ra = 150.12345       # Right Ascension in degrees
    dec = 2.34567        # Declination in degrees
    mjd = 60500.123      # Modified Julian Date of observation
    procver = 1          # Processing version compact ID
    data_release = 0     # 0 = realtime, 1 = DR1, etc.

    print("Input Parameters:")
    print("-" * 40)
    print(f"  RA:           {ra}°")
    print(f"  Dec:          {dec}°")
    print(f"  MJD:          {mjd}")
    print(f"  ProcVer:      {procver}")
    print(f"  DataRelease:  {data_release}")
    print()

    # Generate the spatial_id
    sid = generate_spatial_id(ra, dec, mjd, procver, data_release)

    print("Generated spatial_id:")
    print("-" * 40)
    print(f"  UUID:         {sid}")
    print(f"  Hex:          {sid.hex}")
    print()

    # Show what's encoded in the UUID
    print("Encoded Information (extracted from UUID):")
    print("-" * 40)
    healpix = extract_healpix(sid)
    recovered_ra, recovered_dec = extract_approx_radec(sid)
    recovered_mjd = extract_mjd(sid)
    recovered_procver = extract_procver(sid)
    recovered_dr = extract_data_release(sid)

    print(f"  HEALPix index: {healpix}")
    print(f"  Recovered RA:  {recovered_ra:.6f}° (error: {abs(recovered_ra - ra) * 3600:.4f}\")")
    print(f"  Recovered Dec: {recovered_dec:.6f}° (error: {abs(recovered_dec - dec) * 3600:.4f}\")")
    print(f"  Recovered MJD: {recovered_mjd:.6f} (error: {abs(recovered_mjd - mjd) * 86400:.3f} sec)")
    print(f"  ProcVer:       {recovered_procver}")
    print(f"  DataRelease:   {recovered_dr}")
    print()

    # Show the spatial group for queries
    group = spatial_group_int(sid)
    print("Spatial Group (for SQL queries):")
    print("-" * 40)
    print(f"  group_int:    {group}")
    print(f"  This groups all objects within ~0.05\" of this position")
    print()

    return sid, ra, dec, mjd, procver, data_release


def demo_multiple_observations():
    """
    Show how multiple observations of the same object get the same spatial group.
    """
    print("=" * 70)
    print("STEP 2: Multiple Observations at Same Position")
    print("=" * 70)
    print()

    # Same object observed multiple times
    ra, dec = 150.12345, 2.34567

    print(f"Object Position: RA={ra}°, Dec={dec}°")
    print()

    observations = [
        # (mjd, procver, data_release, description)
        (60500.1, 1, 0, "Night 1, realtime"),
        (60501.2, 1, 0, "Night 2, realtime"),
        (60502.3, 1, 0, "Night 3, realtime"),
        (60500.1, 2, 0, "Night 1, reprocessed with v2"),
        (60500.1, 1, 1, "Night 1, included in DR1"),
    ]

    print("Observations:")
    print("-" * 70)
    print(f"{'Description':<30} {'MJD':<12} {'PV':<4} {'DR':<4} {'Group':<20}")
    print("-" * 70)

    groups = []
    for mjd, pv, dr, desc in observations:
        sid = generate_spatial_id(ra, dec, mjd, pv, dr)
        group = spatial_group_int(sid)
        groups.append(group)
        print(f"{desc:<30} {mjd:<12.1f} {pv:<4} {dr:<4} {group:<20}")

    print("-" * 70)
    all_same = len(set(groups)) == 1
    print(f"All observations share same spatial_group: {all_same}")
    print()
    print("Key insight: spatial_group is based on POSITION only.")
    print("Time, procver, and data_release are encoded but don't affect grouping.")
    print()


def demo_old_vs_new_queries():
    """
    Compare old rootid-based queries with new spatial_id queries.
    """
    print("=" * 70)
    print("STEP 3: Query Comparison - Old (rootid) vs New (spatial_id)")
    print("=" * 70)
    print()

    # Example: User wants light curve for object at RA=150.12345, Dec=2.34567
    ra, dec = 150.12345, 2.34567
    sid = generate_spatial_id(ra, dec, 60500.0, 1, 0)
    group = spatial_group_int(sid)

    print(f"Goal: Get light curve for object at RA={ra}°, Dec={dec}°")
    print()

    print("OLD APPROACH (rootid-based):")
    print("-" * 70)
    print("""
    -- Step 1: Find diaobject by position (cone search)
    SELECT diaobjectid, rootid FROM diaobject
    WHERE q3c_radial_query(ra, dec, {ra}, {dec}, 1.0/3600);

    -- Step 2: Use rootid to find related objects
    -- Problem: rootid is random UUID, requires lookup table
    SELECT diaobjectid FROM diaobject
    WHERE rootid = (SELECT rootid FROM diaobject WHERE diaobjectid = ?);

    -- Step 3: Get light curve
    SELECT midpointtai, psflux FROM diasource
    WHERE diaobjectid IN (SELECT diaobjectid FROM ...);

    Issues:
    - Requires cone search to find initial object
    - rootid is arbitrary, requires join with root_diaobject table
    - In multi-master setup, rootid conflicts possible
    """.format(ra=ra, dec=dec))

    print()
    print("NEW APPROACH (spatial_id-based):")
    print("-" * 70)
    print(f"""
    -- Generate spatial_id from coordinates (done in Python):
    -- sid = generate_spatial_id(ra={ra}, dec={dec}, mjd=60500.0, procver=1, dr=0)
    -- group = spatial_group_int(sid) = {group}

    -- Single query: Find all objects at this position
    SELECT diaobjectid, ra, dec FROM diaobject
    WHERE spatial_group(spatial_id) = {group};

    -- Get light curve for all observations at this position
    SELECT s.midpointtai, s.psflux, o.diaobjectid
    FROM diasource s
    JOIN diaobject o ON s.diaobjectid = o.diaobjectid
    WHERE spatial_group(o.spatial_id) = {group}
    ORDER BY s.midpointtai;

    Benefits:
    - No cone search needed - position encoded in UUID
    - No lookup table - grouping via bitmask
    - Multi-master safe - same coordinates = same spatial_id
    - Indexed: CREATE INDEX ON diaobject(spatial_group(spatial_id))
    """)
    print()


def demo_precision_filtering():
    """
    Show how to filter at different spatial precisions.
    """
    print("=" * 70)
    print("STEP 4: Multi-Precision Filtering")
    print("=" * 70)
    print()

    ra, dec = 150.12345, 2.34567
    sid = generate_spatial_id(ra, dec, 60500.0, 1, 0)
    group = spatial_group_int(sid)
    healpix = extract_healpix(sid)

    print(f"Target: RA={ra}°, Dec={dec}°")
    print(f"HEALPix: {healpix}")
    print(f"spatial_group: {group}")
    print()

    print("Precision Levels (via bit shifting):")
    print("-" * 70)

    precisions = [
        (0, "~0.05\" (default spatial_group)", "Exact object matching"),
        (8, "~0.2\"", "Tight clustering"),
        (16, "~0.8\"", "Typical seeing disk"),
        (24, "~3\"", "Extended source"),
        (32, "~50\"", "Galaxy group"),
    ]

    for shift, resolution, use_case in precisions:
        coarse_group = group >> shift
        print(f"  spatial_group >> {shift:2d} = {coarse_group:<20} ({resolution}, {use_case})")

    print()
    print("SQL Examples:")
    print("-" * 70)
    print(f"""
    -- Exact position match (~0.05")
    WHERE spatial_group(spatial_id) = {group}

    -- Objects within ~0.8" (shift 16 bits)
    WHERE (spatial_group(spatial_id) >> 16) = ({group} >> 16)

    -- Objects within ~50" (shift 32 bits)
    WHERE (spatial_group(spatial_id) >> 32) = ({group} >> 32)
    """)
    print()


def demo_time_filtering():
    """
    Show how to filter by time after position filtering.
    """
    print("=" * 70)
    print("STEP 5: Hierarchical Filtering (Position then Time)")
    print("=" * 70)
    print()

    ra, dec = 150.12345, 2.34567

    # Generate IDs for observations at different times
    obs1 = generate_spatial_id(ra, dec, 60500.0, 1, 0)
    obs2 = generate_spatial_id(ra, dec, 60600.0, 1, 0)
    obs3 = generate_spatial_id(ra, dec, 60700.0, 1, 0)

    print(f"Three observations at RA={ra}°, Dec={dec}°:")
    print("-" * 70)
    print(f"  MJD 60500: {obs1}")
    print(f"  MJD 60600: {obs2}")
    print(f"  MJD 60700: {obs3}")
    print()

    # All have same spatial group
    print(f"All have spatial_group = {spatial_group_int(obs1)}")
    print()

    print("Query Pattern - Find observations in time range:")
    print("-" * 70)
    print(f"""
    -- Step 1: Python generates the spatial_id for target position
    target_sid = generate_spatial_id({ra}, {dec}, 60500.0, 1, 0)
    target_group = spatial_group_int(target_sid)  # = {spatial_group_int(obs1)}

    -- Step 2: SQL finds all observations at position, filters by time
    SELECT o.diaobjectid, o.validitystartmjdtai, s.midpointtai, s.psflux
    FROM diaobject o
    JOIN diasource s ON s.diaobjectid = o.diaobjectid
    WHERE spatial_group(o.spatial_id) = {spatial_group_int(obs1)}
      AND s.midpointtai BETWEEN 60500 AND 60650  -- Time range filter
    ORDER BY s.midpointtai;

    The UUID bit ordering (position → time → procver → dr) means:
    - Position filtering uses the index efficiently
    - Time filtering narrows results after position match
    - procver/dr filtering further refines if needed
    """)
    print()


def demo_workflow_summary():
    """
    Summarize the complete workflow.
    """
    print("=" * 70)
    print("SUMMARY: spatial_id Workflow")
    print("=" * 70)
    print()

    print("""
    UUID Layout (128 bits):
    ┌─────────────────────────────────────────────────────────────────┐
    │ high64: [Reserved:2][HEALPix:62]                                │
    │ low64:  [MJD_ms:43][ProcVer:16][DataRelease:5]                  │
    └─────────────────────────────────────────────────────────────────┘

    Workflow:

    1. INGEST: When importing a diaobject
       ┌──────────────────────────────────────────────────────────────┐
       │ spatial_id = generate_spatial_id(ra, dec, mjd, procver, dr)   │
       │ INSERT INTO diaobject (spatial_id, ra, dec, ...) VALUES (...) │
       └──────────────────────────────────────────────────────────────┘

    2. QUERY: When searching for objects
       ┌──────────────────────────────────────────────────────────────┐
       │ # Python: Generate target spatial_id from coordinates        │
       │ target = generate_spatial_id(ra, dec, any_mjd, any_pv, 0)    │
       │ group = spatial_group_int(target)                            │
       │                                                              │
       │ # SQL: Find all objects at that position                     │
       │ SELECT * FROM diaobject                                      │
       │ WHERE spatial_group(spatial_id) = :group                     │
       └──────────────────────────────────────────────────────────────┘

    3. MULTI-MASTER: Same inputs = Same spatial_id
       ┌──────────────────────────────────────────────────────────────┐
       │ Master A: generate_spatial_id(150.0, 2.0, 60500, 1, 0) = X   │
       │ Master B: generate_spatial_id(150.0, 2.0, 60500, 1, 0) = X   │
       │                                                              │
       │ No conflicts! Both masters generate identical spatial_id.    │
       └──────────────────────────────────────────────────────────────┘
    """)


def main():
    print()
    print("SPATIAL_ID DEMO: From Coordinates to Queries")
    print("=" * 70)
    print()
    print(f"Configuration:")
    print(f"  NSIDE = 2^29 = {NSIDE:,}")
    print(f"  Spatial precision: ~0.0004\" (0.4 milliarcseconds)")
    print(f"  MJD epoch: {MJD_EPOCH} (extends range to year 2280+)")
    print()

    demo_coordinate_to_uuid()
    demo_multiple_observations()
    demo_old_vs_new_queries()
    demo_precision_filtering()
    demo_time_filtering()
    demo_workflow_summary()

    print("=" * 70)
    print("Demo Complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
