#!/usr/bin/env python3
"""
Seed MongoDB with objects that straddle HEALPix pixel boundaries ("fault lines").

This creates pairs of objects that are:
- Within 1 arcsec of each other
- In DIFFERENT HEALPix pixels at the matching NSIDE (2^14)

These test cases verify that the source_importer correctly matches objects
across pixel boundaries by querying neighboring pixels.

Test scenarios:
1. East-West boundary crossing
2. North-South boundary crossing
3. Corner crossing (diagonal neighbors)
4. Base pixel boundary crossing (the trickiest case)
"""

import datetime
import random
import struct
from pymongo import MongoClient

import healpy as hp
import numpy as np


# Match FASTDB's spatial_id.py configuration
NSIDE_MATCHING = 2**14  # ~0.86" cells for matching


def undilate(x: int) -> int:
    """Extract every other bit (compact interleaved bits)."""
    x = x & 0x5555555555555555
    x = (x | (x >> 1)) & 0x3333333333333333
    x = (x | (x >> 2)) & 0x0F0F0F0F0F0F0F0F
    x = (x | (x >> 4)) & 0x00FF00FF00FF00FF
    x = (x | (x >> 8)) & 0x0000FFFF0000FFFF
    x = (x | (x >> 16)) & 0x00000000FFFFFFFF
    return x


def nest_to_xy(nest_idx: int, nside: int) -> tuple[int, int]:
    """Convert NEST index within a base pixel to (x, y) coordinates."""
    npix_per_base = nside * nside
    local_idx = nest_idx % npix_per_base
    x = undilate(local_idx)
    y = undilate(local_idx >> 1)
    return x, y


def get_pixel_boundaries(pixel: int, nside: int) -> dict:
    """Get the RA/Dec boundaries of a HEALPix pixel."""
    # Get the 4 corners of the pixel
    boundaries = hp.boundaries(nside, pixel, step=1, nest=True)
    # boundaries is 3xN array of (x,y,z) unit vectors

    # Convert to RA/Dec
    theta, phi = hp.vec2ang(boundaries.T)
    ra = np.degrees(phi)
    dec = 90 - np.degrees(theta)

    return {
        'ra_min': ra.min(),
        'ra_max': ra.max(),
        'dec_min': dec.min(),
        'dec_max': dec.max(),
        'corners_ra': ra,
        'corners_dec': dec,
    }


def find_boundary_pair(base_ra: float, base_dec: float, boundary_type: str) -> tuple[tuple, tuple, int, int]:
    """
    Find a pair of positions that straddle a pixel boundary.

    Returns: ((ra1, dec1), (ra2, dec2), pixel1, pixel2)
    """
    arcsec = 1.0 / 3600.0

    # Get the pixel containing our base position
    pixel1 = hp.ang2pix(NSIDE_MATCHING, base_ra, base_dec, nest=True, lonlat=True)
    x, y = nest_to_xy(pixel1, NSIDE_MATCHING)

    # Get pixel boundaries to find the edge
    bounds = get_pixel_boundaries(pixel1, NSIDE_MATCHING)

    # Pixel size at this NSIDE is approximately:
    # 206265" / NSIDE = 206265 / 16384 ≈ 12.6" per side
    # But we want to place objects ~0.3-0.5" from the boundary on each side

    offset_from_boundary = 0.4 * arcsec  # 0.4 arcsec from boundary

    if boundary_type == 'east_west':
        # Place objects straddling the east edge
        edge_ra = bounds['ra_max']
        mid_dec = (bounds['dec_min'] + bounds['dec_max']) / 2

        ra1 = edge_ra - offset_from_boundary  # Just inside
        ra2 = edge_ra + offset_from_boundary  # Just outside (next pixel)
        dec1 = mid_dec
        dec2 = mid_dec + random.uniform(-0.1, 0.1) * arcsec  # Slight offset

    elif boundary_type == 'north_south':
        # Place objects straddling the north edge
        mid_ra = (bounds['ra_min'] + bounds['ra_max']) / 2
        edge_dec = bounds['dec_max']

        ra1 = mid_ra
        ra2 = mid_ra + random.uniform(-0.1, 0.1) * arcsec
        dec1 = edge_dec - offset_from_boundary  # Just inside
        dec2 = edge_dec + offset_from_boundary  # Just outside

    elif boundary_type == 'corner':
        # Place objects straddling a corner (diagonal)
        edge_ra = bounds['ra_max']
        edge_dec = bounds['dec_max']

        ra1 = edge_ra - offset_from_boundary
        dec1 = edge_dec - offset_from_boundary
        ra2 = edge_ra + offset_from_boundary
        dec2 = edge_dec + offset_from_boundary

    else:
        raise ValueError(f"Unknown boundary_type: {boundary_type}")

    # Verify they're in different pixels
    pixel2 = hp.ang2pix(NSIDE_MATCHING, ra2, dec2, nest=True, lonlat=True)

    # Calculate separation
    separation = hp.rotator.angdist([ra1, dec1], [ra2, dec2], lonlat=True)
    separation_arcsec = np.degrees(separation) * 3600

    return (ra1, dec1), (ra2, dec2), int(pixel1), int(pixel2), float(separation_arcsec)


def find_base_pixel_boundary() -> tuple[tuple, tuple, int, int, float]:
    """
    Find positions straddling a base pixel boundary.
    This is the trickiest case as it requires the lookup table.
    """
    arcsec = 1.0 / 3600.0

    # Base pixels 0 and 4 share a boundary
    # Base pixel 0 covers roughly dec > 41.8° in certain RA ranges
    # Let's find the boundary between base pixels 0 and 4

    # Try positions near known base pixel boundaries
    # The boundary between polar cap (0-3) and equatorial (4-7) is around dec ≈ 41.8°

    test_dec = 41.8  # Near base pixel boundary
    test_ra = 45.0   # Middle of base pixel 0's RA range

    # Search for the exact boundary by binary search
    dec_low, dec_high = 40.0, 44.0

    for _ in range(50):  # Binary search iterations
        mid_dec = (dec_low + dec_high) / 2
        pixel = hp.ang2pix(NSIDE_MATCHING, test_ra, mid_dec, nest=True, lonlat=True)
        base = pixel // (NSIDE_MATCHING * NSIDE_MATCHING)

        if base < 4:  # In polar cap
            dec_high = mid_dec
        else:  # In equatorial
            dec_low = mid_dec

    boundary_dec = (dec_low + dec_high) / 2

    # Place objects straddling this boundary
    offset = 0.4 * arcsec

    ra1 = test_ra
    dec1 = boundary_dec + offset  # In polar cap (base 0-3)
    ra2 = test_ra + random.uniform(-0.1, 0.1) * arcsec
    dec2 = boundary_dec - offset  # In equatorial (base 4-7)

    pixel1 = int(hp.ang2pix(NSIDE_MATCHING, ra1, dec1, nest=True, lonlat=True))
    pixel2 = int(hp.ang2pix(NSIDE_MATCHING, ra2, dec2, nest=True, lonlat=True))

    base1 = pixel1 // (NSIDE_MATCHING * NSIDE_MATCHING)
    base2 = pixel2 // (NSIDE_MATCHING * NSIDE_MATCHING)

    separation = hp.rotator.angdist([ra1, dec1], [ra2, dec2], lonlat=True)
    separation_arcsec = float(np.degrees(separation) * 3600)

    return (ra1, dec1), (ra2, dec2), pixel1, pixel2, base1, base2, separation_arcsec


def create_flag_dict():
    """Create a dict with all flags set to False."""
    return {f: False for f in [
        "flag_negativeVariance", "flag_calibVariance", "flag_maxIter",
        "flag_edgePix", "flag_intpPix", "flag_satPix", "flag_crPix",
        "flag_bpPix", "flag_badPix", "flag_dpPix", "flag_rcPix", "flag_nan",
        "pixelflags_intpAny", "pixelflags_intpCenter", "pixelflags_edgeAny",
        "pixelflags_edgeCenter", "pixelflags_crAny", "pixelflags_crCenter",
        "pixelflags_bpAny", "pixelflags_bpCenter", "pixelflags_satAny",
        "pixelflags_satCenter", "pixelflags_dpAny", "pixelflags_dpCenter"
    ]}


def generate_source(source_id, object_id, ra, dec, mjd, flux, flux_err, band):
    """Generate a diaSource document.

    Note: visit and detector are deterministic based on source_id to ensure
    re-running the seed script produces identical data (enabling ON CONFLICT
    DO NOTHING to work correctly in the importer).
    """
    return {
        "diaSourceId": source_id,
        "visit": 100000 + (source_id % 10000),  # Deterministic based on source_id
        "detector": source_id % 189,  # Deterministic based on source_id
        "diaObjectId": object_id,
        "ssObjectId": None,
        "parentDiaSourceId": None,
        "midpointMjdTai": mjd,
        "ra": ra,
        "raErr": random.uniform(0.0001, 0.0003),
        "dec": dec,
        "decErr": random.uniform(0.0001, 0.0003),
        "ra_dec_Cov": random.uniform(-1e-8, 1e-8),
        "x": random.uniform(1000, 3000),
        "xErr": random.uniform(0.1, 0.5),
        "y": random.uniform(1000, 3000),
        "yErr": random.uniform(0.1, 0.5),
        "apFlux": flux * random.uniform(0.95, 1.05),
        "apFluxErr": flux_err,
        "snr": flux / flux_err,
        "psfFlux": flux,
        "psfFluxErr": flux_err,
        "psfLnL": random.uniform(-50, -10),
        "psfChi2": random.uniform(0.8, 1.5),
        "psfNdata": random.randint(30, 80),
        "scienceFlux": flux,
        "scienceFluxErr": flux_err,
        "templateFlux": random.uniform(100, 500),
        "templateFluxErr": random.uniform(20, 50),
        "ixx": random.uniform(1.5, 2.5),
        "iyy": random.uniform(1.5, 2.5),
        "ixy": random.uniform(-0.3, 0.3),
        "ixxPSF": random.uniform(1.6, 2.0),
        "iyyPSF": random.uniform(1.6, 2.0),
        "ixyPSF": random.uniform(-0.1, 0.1),
        "extendedness": random.uniform(0.0, 0.15),
        "reliability": random.uniform(0.9, 0.99),
        "band": band,
        "timeProcessedMjdTai": mjd + 0.02,
        "timeWithdrawnMjdTai": None,
        "bboxSize": 41,
        **create_flag_dict()
    }


def generate_alert_document(object_id, source_id, ra, dec, mjd, band, flux, scenario_name):
    """Generate a complete alert document."""
    flux_err = flux * random.uniform(0.02, 0.05)
    now = datetime.datetime.now(tz=datetime.timezone.utc)

    return {
        "topic": "alerts-boundary-test",
        "msgoffset": random.randint(0, 1000000),
        "timestamp": now,
        "savetime": now,
        "scenario": scenario_name,  # Tag for easier identification
        "msg": {
            "diaObject": {
                "diaObjectId": object_id,
                "validityStartMjdTai": mjd,
                "ra": ra,
                "raErr": random.uniform(0.0001, 0.0003),
                "dec": dec,
                "decErr": random.uniform(0.0001, 0.0003),
                "ra_dec_Cov": random.uniform(-1e-8, 1e-8),
                "nDiaSources": 1,
            },
            "diaSource": generate_source(
                source_id=source_id,
                object_id=object_id,
                ra=ra, dec=dec, mjd=mjd,
                flux=flux, flux_err=flux_err, band=band
            ),
            "prvDiaSources": [],
            "prvDiaForcedSources": [],
            "cutoutScience": None,
            "cutoutTemplate": None,
            "cutoutDifference": None,
        }
    }


def main():
    # Connect to MongoDB
    connstr = "mongodb://alertwriter:writer@mongodb:27017/?authSource=brokeralert"
    client = MongoClient(connstr)
    db = client["brokeralert"]
    collection = db["test_alerts"]

    # Clear existing boundary test data
    result = collection.delete_many({"msg.diaObject.diaObjectId": {"$gte": 92000000, "$lt": 93000000}})
    print(f"Cleared {result.deleted_count} existing boundary test documents")

    documents = []
    base_object_id = 92000000
    base_mjd = 60100.0
    bands = ["g", "r", "i", "z"]

    print("\n" + "="*80)
    print("HEALPIX BOUNDARY TEST CASES")
    print(f"NSIDE_MATCHING = {NSIDE_MATCHING} (~{206265/NSIDE_MATCHING:.2f}\" pixel size)")
    print("="*80)

    test_cases = []

    # ==========================================================================
    # Scenario 1: East-West boundary crossings at different sky positions
    # ==========================================================================
    print("\n--- Scenario 1: East-West Boundary Crossings ---")
    for i, (base_ra, base_dec) in enumerate([(120.0, 45.0), (240.0, -30.0), (60.0, 0.0)]):
        pos1, pos2, pix1, pix2, sep = find_boundary_pair(base_ra, base_dec, 'east_west')

        # First object (inside pixel)
        obj_id_1 = base_object_id + i * 10
        test_cases.append({
            'scenario': f'east_west_{i}',
            'object_id': obj_id_1,
            'ra': pos1[0], 'dec': pos1[1],
            'pixel': pix1,
            'pair_id': obj_id_1 + 1,
            'separation': sep,
        })
        documents.append(generate_alert_document(
            obj_id_1, obj_id_1 * 10, pos1[0], pos1[1],
            base_mjd + i, bands[i % 4], 30000.0, f'east_west_{i}_inside'
        ))

        # Second object (outside pixel, should match via neighbor query)
        obj_id_2 = obj_id_1 + 1
        test_cases.append({
            'scenario': f'east_west_{i}',
            'object_id': obj_id_2,
            'ra': pos2[0], 'dec': pos2[1],
            'pixel': pix2,
            'pair_id': obj_id_1,
            'separation': sep,
        })
        documents.append(generate_alert_document(
            obj_id_2, obj_id_2 * 10, pos2[0], pos2[1],
            base_mjd + i + 0.5, bands[(i+1) % 4], 32000.0, f'east_west_{i}_outside'
        ))

        print(f"  Pair {i}: sep={sep:.3f}\", pixels={pix1} vs {pix2}, "
              f"same_pixel={pix1==pix2}")

    # ==========================================================================
    # Scenario 2: North-South boundary crossings
    # ==========================================================================
    print("\n--- Scenario 2: North-South Boundary Crossings ---")
    base_object_id_ns = 92000100
    for i, (base_ra, base_dec) in enumerate([(180.0, 60.0), (90.0, -45.0), (270.0, 15.0)]):
        pos1, pos2, pix1, pix2, sep = find_boundary_pair(base_ra, base_dec, 'north_south')

        obj_id_1 = base_object_id_ns + i * 10
        test_cases.append({
            'scenario': f'north_south_{i}',
            'object_id': obj_id_1,
            'ra': pos1[0], 'dec': pos1[1],
            'pixel': pix1,
            'pair_id': obj_id_1 + 1,
            'separation': sep,
        })
        documents.append(generate_alert_document(
            obj_id_1, obj_id_1 * 10, pos1[0], pos1[1],
            base_mjd + 10 + i, bands[i % 4], 28000.0, f'north_south_{i}_inside'
        ))

        obj_id_2 = obj_id_1 + 1
        test_cases.append({
            'scenario': f'north_south_{i}',
            'object_id': obj_id_2,
            'ra': pos2[0], 'dec': pos2[1],
            'pixel': pix2,
            'pair_id': obj_id_1,
            'separation': sep,
        })
        documents.append(generate_alert_document(
            obj_id_2, obj_id_2 * 10, pos2[0], pos2[1],
            base_mjd + 10 + i + 0.5, bands[(i+1) % 4], 29000.0, f'north_south_{i}_outside'
        ))

        print(f"  Pair {i}: sep={sep:.3f}\", pixels={pix1} vs {pix2}, "
              f"same_pixel={pix1==pix2}")

    # ==========================================================================
    # Scenario 3: Corner crossings (diagonal neighbors)
    # ==========================================================================
    print("\n--- Scenario 3: Corner Crossings (Diagonal) ---")
    base_object_id_corner = 92000200
    for i, (base_ra, base_dec) in enumerate([(150.0, 30.0), (300.0, -60.0)]):
        pos1, pos2, pix1, pix2, sep = find_boundary_pair(base_ra, base_dec, 'corner')

        obj_id_1 = base_object_id_corner + i * 10
        test_cases.append({
            'scenario': f'corner_{i}',
            'object_id': obj_id_1,
            'ra': pos1[0], 'dec': pos1[1],
            'pixel': pix1,
            'pair_id': obj_id_1 + 1,
            'separation': sep,
        })
        documents.append(generate_alert_document(
            obj_id_1, obj_id_1 * 10, pos1[0], pos1[1],
            base_mjd + 20 + i, bands[i % 4], 35000.0, f'corner_{i}_inside'
        ))

        obj_id_2 = obj_id_1 + 1
        test_cases.append({
            'scenario': f'corner_{i}',
            'object_id': obj_id_2,
            'ra': pos2[0], 'dec': pos2[1],
            'pixel': pix2,
            'pair_id': obj_id_1,
            'separation': sep,
        })
        documents.append(generate_alert_document(
            obj_id_2, obj_id_2 * 10, pos2[0], pos2[1],
            base_mjd + 20 + i + 0.5, bands[(i+1) % 4], 34000.0, f'corner_{i}_outside'
        ))

        print(f"  Pair {i}: sep={sep:.3f}\", pixels={pix1} vs {pix2}, "
              f"same_pixel={pix1==pix2}")

    # ==========================================================================
    # Scenario 4: Base pixel boundary crossing (hardest case)
    # ==========================================================================
    print("\n--- Scenario 4: Base Pixel Boundary Crossing ---")
    base_object_id_base = 92000300

    result = find_base_pixel_boundary()
    pos1, pos2, pix1, pix2, base1, base2, sep = result

    obj_id_1 = base_object_id_base
    test_cases.append({
        'scenario': 'base_pixel_boundary',
        'object_id': obj_id_1,
        'ra': pos1[0], 'dec': pos1[1],
        'pixel': pix1,
        'base_pixel': base1,
        'pair_id': obj_id_1 + 1,
        'separation': sep,
    })
    documents.append(generate_alert_document(
        obj_id_1, obj_id_1 * 10, pos1[0], pos1[1],
        base_mjd + 30, "r", 40000.0, 'base_pixel_inside'
    ))

    obj_id_2 = obj_id_1 + 1
    test_cases.append({
        'scenario': 'base_pixel_boundary',
        'object_id': obj_id_2,
        'ra': pos2[0], 'dec': pos2[1],
        'pixel': pix2,
        'base_pixel': base2,
        'pair_id': obj_id_1,
        'separation': sep,
    })
    documents.append(generate_alert_document(
        obj_id_2, obj_id_2 * 10, pos2[0], pos2[1],
        base_mjd + 30.5, "i", 41000.0, 'base_pixel_outside'
    ))

    print(f"  Base pixel boundary: sep={sep:.3f}\", "
          f"pixels={pix1} (base {base1}) vs {pix2} (base {base2})")

    # ==========================================================================
    # Insert into MongoDB
    # ==========================================================================
    result = collection.insert_many(documents)
    print(f"\n{'='*80}")
    print(f"Inserted {len(result.inserted_ids)} alert documents")

    # ==========================================================================
    # Print summary for verification
    # ==========================================================================
    print(f"\n{'='*80}")
    print("TEST CASE SUMMARY")
    print("="*80)
    print(f"{'Object ID':<12} {'Scenario':<25} {'Pixel':<15} {'Sep':<8} {'Pair':<12}")
    print("-"*80)
    for tc in test_cases:
        base_info = f" (base {tc.get('base_pixel', '?')})" if 'base_pixel' in tc else ""
        print(f"{tc['object_id']:<12} {tc['scenario']:<25} {tc['pixel']:<15}{base_info} "
              f"{tc['separation']:.3f}\"   {tc['pair_id']:<12}")

    print(f"\n{'='*80}")
    print("EXPECTED BEHAVIOR")
    print("="*80)
    print("""
When source_importer processes these alerts:

1. CORRECT behavior (using get_neighbor_keys):
   - Each pair should be recognized as the SAME object
   - Second alert in each pair should match the first
   - Result: ~8 root objects (one per pair)

2. INCORRECT behavior (single pixel lookup):
   - Pairs would be treated as DIFFERENT objects
   - Result: ~16 root objects (no matching across boundaries)

To import:
    python -m services.source_importer -p realtime -c test_alerts

Then verify with:
    SELECT diaobjectid, rootid, ra, dec FROM diaobject
    WHERE diaobjectid >= 92000000 AND diaobjectid < 93000000
    ORDER BY diaobjectid;

Check if paired objects share the same rootid (correct) or have different rootids (bug).
""")


if __name__ == "__main__":
    main()
