#!/usr/bin/env python3
"""
Benchmark: Spatial ID vs Cone Search Performance

This script demonstrates the real performance differences between:
1. Cone search (q3c-style trigonometric lookups)
2. Spatial range queries (spatial_group integer comparisons)

Run with: python scripts/benchmark_spatial_vs_cone.py [--with-db]

The --with-db flag runs PostgreSQL benchmarks (requires running FASTDB instance).
Without it, runs pure Python computation benchmarks.
"""

import sys
import os
import time
import argparse
import random
import math
from typing import List, Tuple
import statistics

sys.path.insert(0, 'src')

from spatial_id import (
    generate_spatial_id,
    spatial_group_int,
    extract_healpix,
    NSIDE,
)

# For consistent benchmarks
random.seed(42)


# =============================================================================
# Pure Computation Benchmarks (no database required)
# =============================================================================

def great_circle_distance(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """
    Compute great-circle angular distance between two points.
    This is what q3c does internally for each candidate row.

    Returns distance in arcseconds.
    """
    ra1_rad = math.radians(ra1)
    dec1_rad = math.radians(dec1)
    ra2_rad = math.radians(ra2)
    dec2_rad = math.radians(dec2)

    # Haversine formula
    cos_d = (math.sin(dec1_rad) * math.sin(dec2_rad) +
             math.cos(dec1_rad) * math.cos(dec2_rad) * math.cos(ra1_rad - ra2_rad))

    # Clamp to valid range for acos
    cos_d = max(-1.0, min(1.0, cos_d))
    d_rad = math.acos(cos_d)

    return math.degrees(d_rad) * 3600  # Convert to arcseconds


def cone_search_filter(target_ra: float, target_dec: float,
                       candidates: List[Tuple[float, float]],
                       radius_arcsec: float) -> List[int]:
    """
    Simulate cone search: filter candidates within radius of target.
    Returns indices of matching candidates.
    """
    matches = []
    for i, (ra, dec) in enumerate(candidates):
        dist = great_circle_distance(target_ra, target_dec, ra, dec)
        if dist <= radius_arcsec:
            matches.append(i)
    return matches


def spatial_group_filter(target_group: int,
                         candidate_groups: List[int]) -> List[int]:
    """
    Simulate spatial_group query: filter candidates with matching group.
    Returns indices of matching candidates.
    """
    matches = []
    for i, group in enumerate(candidate_groups):
        if group == target_group:
            matches.append(i)
    return matches


def generate_test_objects(n: int, center_ra: float, center_dec: float,
                          spread_arcsec: float) -> List[Tuple[float, float, int]]:
    """
    Generate n test objects clustered around a center point.
    Returns list of (ra, dec, spatial_group).
    """
    objects = []
    spread_deg = spread_arcsec / 3600.0

    for _ in range(n):
        # Random offset from center
        ra = center_ra + random.gauss(0, spread_deg)
        dec = center_dec + random.gauss(0, spread_deg)

        # Clamp dec to valid range
        dec = max(-90, min(90, dec))

        # Generate spatial_id and extract group
        sid = generate_spatial_id(ra, dec, 60000.0, 1, 0)
        group = spatial_group_int(sid)

        objects.append((ra, dec, group))

    return objects


def benchmark_computation(n_objects: int = 100000, n_queries: int = 1000):
    """
    Benchmark pure computation: trig vs integer comparison.
    """
    print("=" * 70)
    print("BENCHMARK 1: Pure Computation (Trig vs Integer)")
    print("=" * 70)
    print()
    print("Why this matters:")
    print("  Cone search (q3c) evaluates sin/cos/acos for EVERY candidate row.")
    print("  spatial_group replaces that with a single integer equality check.")
    print("  This benchmark isolates the per-row filtering cost to show the")
    print("  raw computational advantage before any database indexing effects.")
    print()
    print("What to look for:")
    print("  The speedup factor shows how much faster integer comparison is")
    print("  than trigonometric distance calculation per candidate row.")
    print()
    print(f"Setup: {n_objects:,} candidate objects, {n_queries:,} queries")
    print()

    # Generate test data
    print("Generating test objects...")
    center_ra, center_dec = 150.0, 2.0
    objects = generate_test_objects(n_objects, center_ra, center_dec, spread_arcsec=60.0)

    # Separate coordinates and groups
    coords = [(ra, dec) for ra, dec, _ in objects]
    groups = [group for _, _, group in objects]

    # Generate query targets (subset of objects + some random)
    query_targets = []
    for i in range(n_queries):
        if i < n_queries // 2:
            # Use existing object positions
            idx = random.randint(0, n_objects - 1)
            ra, dec, group = objects[idx]
        else:
            # Random positions near center
            ra = center_ra + random.gauss(0, 60.0/3600)
            dec = center_dec + random.gauss(0, 60.0/3600)
            sid = generate_spatial_id(ra, dec, 60000.0, 1, 0)
            group = spatial_group_int(sid)
        query_targets.append((ra, dec, group))

    # Benchmark cone search (trig per candidate)
    print("Running cone search benchmark (trig calculations)...")
    cone_times = []
    cone_matches = []

    for ra, dec, _ in query_targets:
        start = time.perf_counter()
        matches = cone_search_filter(ra, dec, coords, radius_arcsec=0.1)
        elapsed = time.perf_counter() - start
        cone_times.append(elapsed)
        cone_matches.append(len(matches))

    # Benchmark spatial_group (integer comparison)
    print("Running spatial_group benchmark (integer comparisons)...")
    spatial_times = []
    spatial_matches = []

    for _, _, target_group in query_targets:
        start = time.perf_counter()
        matches = spatial_group_filter(target_group, groups)
        elapsed = time.perf_counter() - start
        spatial_times.append(elapsed)
        spatial_matches.append(len(matches))

    # Report results
    print()
    print("Results:")
    print("-" * 70)

    cone_avg = statistics.mean(cone_times) * 1000
    cone_std = statistics.stdev(cone_times) * 1000
    spatial_avg = statistics.mean(spatial_times) * 1000
    spatial_std = statistics.stdev(spatial_times) * 1000

    print(f"{'Method':<25} {'Avg (ms)':<15} {'Std (ms)':<15} {'Speedup':<10}")
    print("-" * 70)
    print(f"{'Cone search (trig)':<25} {cone_avg:<15.4f} {cone_std:<15.4f} {'1.0x':<10}")
    print(f"{'Spatial group (int)':<25} {spatial_avg:<15.4f} {spatial_std:<15.4f} {f'{cone_avg/spatial_avg:.1f}x':<10}")
    print()

    print(f"Average matches per query:")
    print(f"  Cone search:    {statistics.mean(cone_matches):.1f}")
    print(f"  Spatial group:  {statistics.mean(spatial_matches):.1f}")
    print()

    return cone_avg, spatial_avg


def benchmark_uuid_vs_bigint(n_comparisons: int = 10000000):
    """
    Benchmark UUID comparison vs BIGINT comparison.
    """
    print("=" * 70)
    print("BENCHMARK 2: UUID vs BIGINT Comparison")
    print("=" * 70)
    print()
    print("Why this matters:")
    print("  spatial_group() extracts a BIGINT (8 bytes) from a UUID (16 bytes).")
    print("  PostgreSQL indexes on BIGINT are smaller and faster to traverse than")
    print("  UUID indexes. This benchmark measures the raw equality-check cost")
    print("  difference between the two types in Python, which mirrors what")
    print("  happens at the CPU level inside PostgreSQL B-tree index scans.")
    print()
    print("What to look for:")
    print("  BIGINT comparisons should be faster due to fewer bytes to compare")
    print("  and better CPU cache utilization (half the memory per key).")
    print()
    print(f"Setup: {n_comparisons:,} comparisons")
    print()

    import uuid

    # Generate test data
    print("Generating test UUIDs and BIGINTs...")
    uuids = [uuid.uuid4() for _ in range(1000)]
    bigints = [random.randint(0, 2**63-1) for _ in range(1000)]

    # Target values
    target_uuid = uuids[500]
    target_bigint = bigints[500]

    # Benchmark UUID comparison
    print("Running UUID comparison benchmark...")
    start = time.perf_counter()
    uuid_matches = 0
    for _ in range(n_comparisons // 1000):
        for u in uuids:
            if u == target_uuid:
                uuid_matches += 1
    uuid_time = time.perf_counter() - start

    # Benchmark BIGINT comparison
    print("Running BIGINT comparison benchmark...")
    start = time.perf_counter()
    bigint_matches = 0
    for _ in range(n_comparisons // 1000):
        for b in bigints:
            if b == target_bigint:
                bigint_matches += 1
    bigint_time = time.perf_counter() - start

    # Report results
    print()
    print("Results:")
    print("-" * 70)
    print(f"{'Method':<25} {'Time (s)':<15} {'Ops/sec':<20} {'Speedup':<10}")
    print("-" * 70)
    print(f"{'UUID comparison':<25} {uuid_time:<15.4f} {n_comparisons/uuid_time:,.0f}{'':<8} {'1.0x':<10}")
    print(f"{'BIGINT comparison':<25} {bigint_time:<15.4f} {n_comparisons/bigint_time:,.0f}{'':<8} {f'{uuid_time/bigint_time:.1f}x':<10}")
    print()

    return uuid_time, bigint_time


def benchmark_spatial_id_generation(n_generations: int = 100000):
    """
    Benchmark spatial_id generation from coordinates.
    """
    print("=" * 70)
    print("BENCHMARK 3: Spatial ID Generation Speed")
    print("=" * 70)
    print()
    print("Why this matters:")
    print("  Every diaobject insert requires generating a spatial_id from")
    print("  (ra, dec, mjd, procver, data_release). This involves a HEALPix")
    print("  pixelization (healpy.ang2pix) plus bit-packing into a UUID.")
    print("  If generation is too slow, it becomes a bottleneck during bulk")
    print("  ingestion from the source_importer pipeline.")
    print()
    print("What to look for:")
    print("  Generation should be sub-millisecond per ID. At ~10 microseconds")
    print("  or less, the cost is negligible compared to DB insert latency.")
    print()
    print(f"Setup: {n_generations:,} ID generations")
    print()

    # Generate random coordinates
    coords = [(random.uniform(0, 360), random.uniform(-90, 90)) for _ in range(n_generations)]

    # Benchmark generation
    print("Running spatial_id generation benchmark...")
    start = time.perf_counter()
    for ra, dec in coords:
        sid = generate_spatial_id(ra, dec, 60000.0, 1, 0)
        group = spatial_group_int(sid)
    gen_time = time.perf_counter() - start

    # Report results
    print()
    print("Results:")
    print("-" * 70)
    print(f"Total time:        {gen_time:.3f} seconds")
    print(f"Per generation:    {gen_time/n_generations*1e6:.2f} microseconds")
    print(f"Generations/sec:   {n_generations/gen_time:,.0f}")
    print()
    print("Note: This is the cost of computing spatial_group from (ra, dec)")
    print("      without any database lookup - done entirely in Python.")
    print()

    return gen_time


# =============================================================================
# PostgreSQL Benchmarks (requires database)
# =============================================================================

def benchmark_with_database():
    """
    Run benchmarks against actual PostgreSQL database.
    """
    print("=" * 70)
    print("BENCHMARK 4: PostgreSQL Query Performance")
    print("=" * 70)
    print()
    print("Why this matters:")
    print("  This is the end-to-end measurement that matters most. It compares")
    print("  three actual PostgreSQL query strategies against real diaobject data:")
    print("    1. q3c cone search  - trig-based spatial index (current standard)")
    print("    2. spatial_group()  - BIGINT functional index on spatial_id")
    print("    3. direct spatial_id lookup - exact UUID match (best-case baseline)")
    print()
    print("What to look for:")
    print("  spatial_group queries should approach direct-lookup speed while")
    print("  finding ALL objects at a position (not just one exact UUID).")
    print("  Cone search is the slowest because it evaluates trig functions")
    print("  even with q3c's spatial index narrowing candidates.")
    print()

    try:
        import db
    except Exception as e:
        print(f"Could not import db module: {e}")
        print("Skipping database benchmarks.")
        return None

    try:
        with db.DB() as con:
            cursor = con.cursor()

            # Check if we have data
            cursor.execute("SELECT COUNT(*) FROM diaobject")
            count = cursor.fetchone()[0]
            print(f"Found {count:,} diaobjects in database")

            if count == 0:
                print("No data in database - skipping benchmarks")
                return None

            # Get sample objects for testing
            cursor.execute("""
                SELECT diaobjectid, ra, dec, spatial_id
                FROM diaobject
                ORDER BY random()
                LIMIT 100
            """)
            sample_objects = cursor.fetchall()

            if not sample_objects:
                print("Could not fetch sample objects")
                return None

            print(f"Testing with {len(sample_objects)} sample objects")
            print()

            # Benchmark 1: Cone search with q3c
            print("Testing cone search (q3c_radial_query)...")
            cone_times = []

            for diaobjectid, ra, dec, spatial_id in sample_objects[:20]:
                start = time.perf_counter()
                cursor.execute("""
                    SELECT diaobjectid, ra, dec
                    FROM diaobject
                    WHERE q3c_radial_query(ra, dec, %s, %s, 1.0/3600)
                """, (ra, dec))
                results = cursor.fetchall()
                elapsed = time.perf_counter() - start
                cone_times.append(elapsed)

            # Benchmark 2: spatial_group query
            print("Testing spatial_group query...")
            spatial_times = []

            for diaobjectid, ra, dec, spatial_id in sample_objects[:20]:
                # Generate spatial_group from coordinates
                sid = generate_spatial_id(ra, dec, 60000.0, 1, 0)
                group = spatial_group_int(sid)

                start = time.perf_counter()
                cursor.execute("""
                    SELECT diaobjectid, ra, dec
                    FROM diaobject
                    WHERE spatial_group(spatial_id) = %s
                """, (group,))
                results = cursor.fetchall()
                elapsed = time.perf_counter() - start
                spatial_times.append(elapsed)

            # Benchmark 3: Direct spatial_id lookup (for comparison)
            print("Testing direct spatial_id lookup...")
            rootid_times = []

            for diaobjectid, ra, dec, spatial_id in sample_objects[:20]:
                start = time.perf_counter()
                cursor.execute("""
                    SELECT diaobjectid, ra, dec
                    FROM diaobject
                    WHERE spatial_id = %s
                """, (spatial_id,))
                results = cursor.fetchall()
                elapsed = time.perf_counter() - start
                rootid_times.append(elapsed)

            # Report results
            print()
            print("Results:")
            print("-" * 70)

            cone_avg = statistics.mean(cone_times) * 1000
            spatial_avg = statistics.mean(spatial_times) * 1000
            rootid_avg = statistics.mean(rootid_times) * 1000

            print(f"{'Method':<35} {'Avg (ms)':<15} {'Speedup vs Cone':<15}")
            print("-" * 70)
            print(f"{'Cone search (q3c)':<35} {cone_avg:<15.3f} {'1.0x':<15}")
            print(f"{'spatial_group(spatial_id)':<35} {spatial_avg:<15.3f} {f'{cone_avg/spatial_avg:.1f}x':<15}")
            print(f"{'Direct spatial_id lookup':<35} {rootid_avg:<15.3f} {f'{cone_avg/rootid_avg:.1f}x':<15}")
            print()

            return cone_avg, spatial_avg, rootid_avg

    except Exception as e:
        print(f"Database benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def benchmark_index_size():
    """
    Compare theoretical index sizes.
    """
    print("=" * 70)
    print("BENCHMARK 5: Index Size Comparison")
    print("=" * 70)
    print()
    print("Why this matters:")
    print("  LSST will produce ~10 billion diaobjects over its survey lifetime.")
    print("  Index size directly affects query performance: smaller indexes fit")
    print("  more entries in RAM and CPU cache, reducing disk I/O during scans.")
    print("  A BIGINT spatial_group index is half the size of a UUID index.")
    print()
    print("What to look for:")
    print("  At 1 billion rows, the BIGINT index saves ~10 GB over UUID.")
    print("  This is the difference between fitting the hot working set in")
    print("  memory vs spilling to disk on typical database servers.")
    print()

    # Typical B-tree overhead is ~30% for internal nodes
    btree_overhead = 1.3

    for n_rows in [1_000_000, 10_000_000, 100_000_000, 1_000_000_000]:
        uuid_size = n_rows * 16 * btree_overhead  # 16 bytes per UUID
        bigint_size = n_rows * 8 * btree_overhead  # 8 bytes per BIGINT

        print(f"For {n_rows:>15,} rows:")
        print(f"  UUID index:   {uuid_size / 1e9:>8.2f} GB")
        print(f"  BIGINT index: {bigint_size / 1e9:>8.2f} GB")
        print(f"  Savings:      {(uuid_size - bigint_size) / 1e9:>8.2f} GB ({(1 - bigint_size/uuid_size)*100:.0f}%)")
        print()


def benchmark_no_lookup_advantage():
    """
    Demonstrate the advantage of not needing a database lookup.
    """
    print("=" * 70)
    print("BENCHMARK 6: Lookup-Free Advantage")
    print("=" * 70)
    print()
    print("Why this matters:")
    print("  The old workflow required a DB round-trip to resolve coordinates")
    print("  to a root_diaobject_id before querying related objects. With")
    print("  spatial_id, the group key is computed directly from (ra, dec)")
    print("  in Python -- no database call needed. This eliminates a full")
    print("  network round-trip per request, which dominates at high throughput.")
    print()
    print("What to look for:")
    print("  The speedup is dominated by network latency elimination, not CPU.")
    print("  Even at 1ms round-trip (fast LAN), skipping 10,000 lookups saves")
    print("  10 seconds. At typical cloud latencies (2-5ms) the savings grow.")
    print()

    # Simulate the old workflow (need DB lookup) vs new workflow (compute directly)
    n_requests = 10000

    # Simulated DB round-trip latency (typical LAN: 0.5-2ms)
    db_latency_ms = 1.0

    print(f"Scenario: Processing {n_requests:,} spectrum requests")
    print(f"Assumed DB round-trip latency: {db_latency_ms} ms")
    print()

    # Old workflow: cone search to find root_diaobject_id
    old_time_per_request = db_latency_ms  # At least one DB query
    old_total = old_time_per_request * n_requests

    # New workflow: compute spatial_group directly
    coords = [(random.uniform(0, 360), random.uniform(-90, 90)) for _ in range(n_requests)]

    start = time.perf_counter()
    for ra, dec in coords:
        sid = generate_spatial_id(ra, dec, 60000.0, 1, 0)
        group = spatial_group_int(sid)
    new_compute_time = (time.perf_counter() - start) * 1000  # Convert to ms

    print("Results:")
    print("-" * 70)
    print(f"{'Workflow':<40} {'Total Time':<20}")
    print("-" * 70)
    print(f"{'Old (DB lookup per request)':<40} {old_total:>10,.0f} ms ({old_total/1000:.1f} sec)")
    print(f"{'New (compute spatial_group)':<40} {new_compute_time:>10,.1f} ms ({new_compute_time/1000:.3f} sec)")
    print("-" * 70)
    print(f"{'Speedup':<40} {old_total/new_compute_time:>10,.0f}x")
    print()

    print("Key insight: The new workflow eliminates the DB round-trip entirely")
    print("for determining the object group. The spatial_group is computed")
    print("directly from coordinates in ~microseconds, not milliseconds.")
    print()


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark spatial_id vs cone search performance"
    )
    parser.add_argument(
        "--with-db",
        action="store_true",
        help="Run PostgreSQL benchmarks (requires running FASTDB instance)"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run quick benchmarks with fewer iterations"
    )
    args = parser.parse_args()

    # Adjust iterations for quick mode
    if args.quick:
        n_objects = 10000
        n_queries = 100
        n_comparisons = 1000000
        n_generations = 10000
    else:
        n_objects = 100000
        n_queries = 1000
        n_comparisons = 10000000
        n_generations = 100000

    print()
    print("=" * 70)
    print("SPATIAL ID vs CONE SEARCH BENCHMARK SUITE")
    print("=" * 70)
    print()
    print("This benchmark demonstrates the performance differences between:")
    print("  1. Traditional cone search (trigonometric calculations)")
    print("  2. Spatial group queries (integer comparisons)")
    print()
    if args.quick:
        print("Running in QUICK mode (fewer iterations)")
        print()

    # Run pure computation benchmarks
    benchmark_computation(n_objects=n_objects, n_queries=n_queries)
    benchmark_uuid_vs_bigint(n_comparisons=n_comparisons)
    benchmark_spatial_id_generation(n_generations=n_generations)
    benchmark_index_size()
    benchmark_no_lookup_advantage()

    # Run database benchmarks if requested
    if args.with_db:
        benchmark_with_database()
    else:
        print("=" * 70)
        print("DATABASE BENCHMARKS SKIPPED")
        print("=" * 70)
        print()
        print("To run PostgreSQL benchmarks, use: --with-db")
        print("Requires a running FASTDB instance with data.")
        print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()
    print("The benchmarks demonstrate that spatial_id queries are faster because:")
    print()
    print("1. INTEGER COMPARISON vs TRIG:")
    print("   - Cone search computes sin/cos/acos per candidate row")
    print("   - Spatial group uses simple integer equality (=)")
    print()
    print("2. SMALLER INDEX:")
    print("   - UUID: 16 bytes per entry")
    print("   - BIGINT: 8 bytes per entry (50% smaller, better cache)")
    print()
    print("3. NO LOOKUP REQUIRED:")
    print("   - Old: Must query DB to find root_diaobject_id from coordinates")
    print("   - New: Compute spatial_group directly from (ra, dec) in Python")
    print("   - Eliminates entire DB round-trip (~1ms+ latency)")
    print()
    print("4. DETERMINISTIC:")
    print("   - Same (ra, dec) always produces same spatial_group")
    print("   - Safe for multi-master replication")
    print()


if __name__ == "__main__":
    main()
