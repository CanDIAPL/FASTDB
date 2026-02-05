#!/usr/bin/env python
"""
Generate synthetic alert data for multi-writer demo.

Creates MongoDB documents in the format expected by source_importer:
{"msg": {"diaObject": {...}, "diaSource": {...}}, "savetime": datetime}

The data is partitioned by time ranges with 30% overlap to test
conflict handling with deterministic spatial_id generation.
"""

import sys
import os
import datetime
import random
import argparse

# Add FASTDB directories to path for imports
# Works both locally (src/) and in container (/fastdb/)
_base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(_base, 'src'))
sys.path.insert(0, '/fastdb')

import db


def generate_test_objects(n_objects=100, seed=42):
    """Generate n_objects unique sky positions.

    Returns list of dicts with ra, dec, diaObjectId.
    Positions are distributed across the sky.
    """
    random.seed(seed)
    objects = []

    for i in range(n_objects):
        # Spread objects across different sky regions
        ra = random.uniform(0, 360)
        # Use arcsin distribution for uniform sky coverage
        dec = random.uniform(-90, 90)

        objects.append({
            'diaObjectId': 1000000 + i,
            'ra': ra,
            'dec': dec,
        })

    return objects


def generate_alert_document(obj, mjd, source_id):
    """Generate a single alert document in the expected format.

    Parameters
    ----------
    obj : dict
        Object with diaObjectId, ra, dec
    mjd : float
        Modified Julian Date for this detection
    source_id : int
        Unique source ID

    Returns
    -------
    dict
        Alert document ready for MongoDB insertion
    """
    return {
        "msg": {
            "diaObject": {
                "diaObjectId": obj['diaObjectId'],
                "validityStartMjdTai": mjd,
                "ra": obj['ra'],
                "raErr": 0.001,
                "dec": obj['dec'],
                "decErr": 0.001,
                "ra_dec_Cov": 0.0,
            },
            "diaSource": {
                "diaSourceId": source_id,
                "visit": source_id,
                "detector": 1,
                "diaObjectId": obj['diaObjectId'],
                "ssObjectId": None,
                "parentDiaSourceId": None,
                "midpointMjdTai": mjd,
                "ra": obj['ra'],
                "raErr": 0.001,
                "dec": obj['dec'],
                "decErr": 0.001,
                "ra_dec_Cov": 0.0,
                "x": 1024.0,
                "xErr": 0.1,
                "y": 1024.0,
                "yErr": 0.1,
                "apFlux": 1000.0,
                "apFluxErr": 10.0,
                "snr": 100.0,
                "psfFlux": 1000.0,
                "psfFluxErr": 10.0,
                "psfLnL": -100.0,
                "psfChi2": 1.0,
                "psfNdata": 100,
                "scienceFlux": 1000.0,
                "scienceFluxErr": 10.0,
                "templateFlux": 0.0,
                "templateFluxErr": 1.0,
                "ixx": 1.0,
                "iyy": 1.0,
                "ixy": 0.0,
                "ixxPSF": 1.0,
                "iyyPSF": 1.0,
                "ixyPSF": 0.0,
                "extendedness": 0.0,
                "reliability": 1.0,
                "band": "r",
                "timeProcessedMjdTai": mjd + 0.001,
                "timeWithdrawnMjdTai": None,
                "bboxSize": 41,
                # Flag fields (all False for simplicity)
                "centroid_flag": False,
                "centroidTruth_flag": False,
                "isDipole": False,
                "forced_PsfFlux_flag": False,
                "forced_PsfFlux_flag_edge": False,
                "forced_PsfFlux_flag_noGoodPixels": False,
                "pixelFlags_bad": False,
                "pixelFlags_cr": False,
                "pixelFlags_crCenter": False,
                "pixelFlags_edge": False,
                "pixelFlags_interpolated": False,
                "pixelFlags_interpolatedCenter": False,
                "pixelFlags_saturated": False,
                "pixelFlags_saturatedCenter": False,
                "pixelFlags_suspect": False,
                "pixelFlags_suspectCenter": False,
            },
            "prvDiaSources": [],  # Not using previous sources for simplicity
            "prvDiaForcedSources": [],
        },
        "savetime": datetime.datetime.now(tz=datetime.timezone.utc),
    }


def generate_partitioned_data(objects, detections_per_object=3, n_partitions=3, overlap_fraction=0.3):
    """Generate alert data partitioned with overlap by time.

    Creates data where:
    - Each partition has a distinct time window (savetime)
    - Overlap objects appear in ALL partitions (with same RA/DEC/MJD)
    - This simulates multiple workers processing different time slices

    Parameters
    ----------
    objects : list
        List of object dicts from generate_test_objects
    detections_per_object : int
        Number of MJD detections per object
    n_partitions : int
        Number of partitions to create
    overlap_fraction : float
        Fraction of objects that appear in ALL partitions (0.3 = 30%)

    Returns
    -------
    tuple
        (list of lists of alert documents, list of time boundaries)
    """
    import datetime

    n_objects = len(objects)
    n_overlap = int(n_objects * overlap_fraction)
    n_unique_per_partition = (n_objects - n_overlap) // n_partitions

    # First n_overlap objects appear in ALL partitions
    overlap_objects = objects[:n_overlap]
    unique_objects = objects[n_overlap:]

    # Base MJD (recent-ish)
    base_mjd = 60000.0

    # Time boundaries for partitions (each partition gets 1 hour window)
    base_time = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    time_boundaries = []

    partitions = []
    source_id = 2000000

    for p in range(n_partitions):
        partition_docs = []
        # Each partition's savetime is in a distinct 1-hour window
        partition_savetime = base_time + datetime.timedelta(hours=p)
        time_boundaries.append((
            partition_savetime - datetime.timedelta(seconds=1),
            partition_savetime + datetime.timedelta(hours=1)
        ))

        # Add overlap objects (appear in all partitions with SAME coordinates)
        # This tests the deterministic rootid generation - same (ra,dec,mjd) -> same rootid
        for obj in overlap_objects:
            for d in range(detections_per_object):
                mjd = base_mjd + d
                doc = generate_alert_document(obj, mjd, source_id)
                doc['savetime'] = partition_savetime + datetime.timedelta(minutes=d)
                partition_docs.append(doc)
                source_id += 1

        # Add unique objects for this partition
        start_idx = p * n_unique_per_partition
        end_idx = start_idx + n_unique_per_partition
        for obj in unique_objects[start_idx:end_idx]:
            for d in range(detections_per_object):
                mjd = base_mjd + d
                doc = generate_alert_document(obj, mjd, source_id)
                doc['savetime'] = partition_savetime + datetime.timedelta(minutes=30+d)
                partition_docs.append(doc)
                source_id += 1

        partitions.append(partition_docs)

    return partitions, time_boundaries


def insert_test_data(collection_name, partitions, clear_existing=True):
    """Insert partitioned test data into MongoDB.

    Parameters
    ----------
    collection_name : str
        MongoDB collection name
    partitions : list of lists
        Alert documents by partition
    clear_existing : bool
        If True, drop existing collection first

    Returns
    -------
    dict
        Statistics about inserted data
    """
    with db.MG() as mgclient:
        collection = db.get_mongo_collection(mgclient, collection_name)

        if clear_existing:
            collection.drop()

        total_docs = 0
        for p, docs in enumerate(partitions):
            if docs:
                collection.insert_many(docs)
                total_docs += len(docs)
                print(f"  Partition {p}: {len(docs)} documents")

        # Get unique object count
        unique_objects = collection.distinct("msg.diaObject.diaObjectId")

        return {
            'total_documents': total_docs,
            'unique_objects': len(unique_objects),
            'partitions': len(partitions),
        }


def main():
    parser = argparse.ArgumentParser(
        description='Generate synthetic alert data for multi-writer demo'
    )
    parser.add_argument('-c', '--collection', default='multiwriter_test',
                        help='MongoDB collection name')
    parser.add_argument('-n', '--num-objects', type=int, default=100,
                        help='Number of unique objects')
    parser.add_argument('-d', '--detections', type=int, default=3,
                        help='Detections per object')
    parser.add_argument('-p', '--partitions', type=int, default=3,
                        help='Number of partitions')
    parser.add_argument('-o', '--overlap', type=float, default=0.3,
                        help='Overlap fraction (0.3 = 30%)')
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed for reproducibility')
    parser.add_argument('--no-clear', action='store_true',
                        help='Do not clear existing data')
    args = parser.parse_args()

    print(f"Generating test data:")
    print(f"  Objects: {args.num_objects}")
    print(f"  Detections per object: {args.detections}")
    print(f"  Partitions: {args.partitions}")
    print(f"  Overlap: {args.overlap*100:.0f}%")
    print()

    # Generate objects
    objects = generate_test_objects(args.num_objects, args.seed)

    # Generate partitioned data
    partitions = generate_partitioned_data(
        objects,
        detections_per_object=args.detections,
        n_partitions=args.partitions,
        overlap_fraction=args.overlap
    )

    # Insert into MongoDB
    print(f"Inserting into collection '{args.collection}':")
    stats = insert_test_data(
        args.collection,
        partitions,
        clear_existing=not args.no_clear
    )

    print()
    print("Summary:")
    print(f"  Total documents: {stats['total_documents']}")
    print(f"  Unique objects: {stats['unique_objects']}")
    print(f"  Partitions: {stats['partitions']}")


if __name__ == '__main__':
    main()
