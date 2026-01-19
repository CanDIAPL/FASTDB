#!/usr/bin/env python3
"""
Seed MongoDB with test alert documents for source importer testing.

This script generates synthetic alert documents and inserts them into MongoDB,
allowing you to test the source importer without needing Kafka.

Usage:
    python seed_mongodb_alerts.py --collection test_alerts --num-objects 10

    # Then run the source importer:
    python -m services.source_importer -p realtime -c test_alerts
"""

import argparse
import datetime
import random
import sys

from pymongo import MongoClient


def generate_dia_object(object_id: int, ra: float, dec: float, mjd: float) -> dict:
    """Generate a synthetic diaObject document."""
    return {
        "diaObjectId": object_id,
        "validityStartMjdTai": mjd,
        "ra": ra,
        "raErr": random.uniform(0.0001, 0.001),
        "dec": dec,
        "decErr": random.uniform(0.0001, 0.001),
        "ra_dec_Cov": None,
        "nDiaSources": random.randint(1, 10),
        # Optional flux fields - set to None for simplicity
        "u_psfFluxMean": None,
        "g_psfFluxMean": random.uniform(1000, 100000),
        "r_psfFluxMean": random.uniform(1000, 100000),
        "i_psfFluxMean": random.uniform(1000, 100000),
        "z_psfFluxMean": None,
        "y_psfFluxMean": None,
    }


def generate_dia_source(source_id: int, object_id: int, ra: float, dec: float, mjd: float, band: str) -> dict:
    """Generate a synthetic diaSource document."""
    flux = random.uniform(1000, 100000)
    flux_err = flux * random.uniform(0.01, 0.1)

    return {
        "diaSourceId": source_id,
        "visit": random.randint(1, 1000000),
        "detector": random.randint(0, 188),
        "diaObjectId": object_id,
        "ssObjectId": None,
        "parentDiaSourceId": None,
        "midpointMjdTai": mjd,
        "ra": ra + random.uniform(-0.0001, 0.0001),
        "raErr": random.uniform(0.0001, 0.001),
        "dec": dec + random.uniform(-0.0001, 0.0001),
        "decErr": random.uniform(0.0001, 0.001),
        "ra_dec_Cov": None,
        "x": random.uniform(0, 4096),
        "xErr": random.uniform(0.1, 1.0),
        "y": random.uniform(0, 4096),
        "yErr": random.uniform(0.1, 1.0),
        "apFlux": flux,
        "apFluxErr": flux_err,
        "snr": flux / flux_err,
        "psfFlux": flux,
        "psfFluxErr": flux_err,
        "psfLnL": random.uniform(-100, 0),
        "psfChi2": random.uniform(0.5, 2.0),
        "psfNdata": random.randint(10, 100),
        "scienceFlux": flux,
        "scienceFluxErr": flux_err,
        "templateFlux": random.uniform(100, 1000),
        "templateFluxErr": random.uniform(10, 100),
        "ixx": random.uniform(1, 5),
        "iyy": random.uniform(1, 5),
        "ixy": random.uniform(-1, 1),
        "ixxPSF": random.uniform(1, 3),
        "iyyPSF": random.uniform(1, 3),
        "ixyPSF": random.uniform(-0.5, 0.5),
        "extendedness": random.uniform(0, 1),
        "reliability": random.uniform(0.5, 1.0),
        "band": band,
        "timeProcessedMjdTai": mjd + 0.01,
        "timeWithdrawnMjdTai": None,
        "bboxSize": random.randint(20, 100),
        # Flags - all False for simplicity
        "flag_negativeVariance": False,
        "flag_calibVariance": False,
        "flag_maxIter": False,
        "flag_edgePix": False,
        "flag_intpPix": False,
        "flag_satPix": False,
        "flag_crPix": False,
        "flag_bpPix": False,
        "flag_badPix": False,
        "flag_dpPix": False,
        "flag_rcPix": False,
        "flag_nan": False,
        "pixelflags_intpAny": False,
        "pixelflags_intpCenter": False,
        "pixelflags_edgeAny": False,
        "pixelflags_edgeCenter": False,
        "pixelflags_crAny": False,
        "pixelflags_crCenter": False,
        "pixelflags_bpAny": False,
        "pixelflags_bpCenter": False,
        "pixelflags_satAny": False,
        "pixelflags_satCenter": False,
        "pixelflags_dpAny": False,
        "pixelflags_dpCenter": False,
    }


def generate_dia_forced_source(forced_source_id: int, object_id: int, ra: float, dec: float, mjd: float, band: str) -> dict:
    """Generate a synthetic diaForcedSource document."""
    flux = random.uniform(500, 50000)
    flux_err = flux * random.uniform(0.05, 0.2)

    return {
        "diaForcedSourceId": forced_source_id,
        "diaObjectId": object_id,
        "ra": ra,
        "dec": dec,
        "visit": random.randint(1, 1000000),
        "detector": random.randint(0, 188),
        "psfFlux": flux,
        "psfFluxErr": flux_err,
        "midpointMjdTai": mjd,
        "scienceFlux": flux,
        "scienceFluxErr": flux_err,
        "band": band,
        "timeProcessedMjdTai": mjd + 0.01,
        "timeWithdrawnMjdTai": None,
    }


def generate_alert_document(
    object_id: int,
    source_id: int,
    forced_source_id_start: int,
    ra: float,
    dec: float,
    mjd: float,
    num_prv_sources: int = 3,
    num_prv_forced: int = 5,
) -> dict:
    """Generate a complete alert document for MongoDB."""

    bands = ["u", "g", "r", "i", "z", "y"]
    band = random.choice(bands)

    # Generate the main diaObject and diaSource
    dia_object = generate_dia_object(object_id, ra, dec, mjd)
    dia_source = generate_dia_source(source_id, object_id, ra, dec, mjd, band)

    # Generate previous sources (older observations)
    prv_sources = []
    for i in range(num_prv_sources):
        prv_mjd = mjd - random.uniform(1, 30)
        prv_band = random.choice(bands)
        prv_source = generate_dia_source(
            source_id + i + 1,
            object_id,
            ra,
            dec,
            prv_mjd,
            prv_band
        )
        prv_sources.append(prv_source)

    # Generate previous forced sources
    prv_forced = []
    for i in range(num_prv_forced):
        prv_mjd = mjd - random.uniform(1, 60)
        prv_band = random.choice(bands)
        prv_forced_source = generate_dia_forced_source(
            forced_source_id_start + i,
            object_id,
            ra,
            dec,
            prv_mjd,
            prv_band
        )
        prv_forced.append(prv_forced_source)

    now = datetime.datetime.now(tz=datetime.timezone.utc)

    return {
        "topic": "alerts-test",
        "msgoffset": random.randint(0, 1000000),
        "timestamp": now - datetime.timedelta(seconds=random.randint(0, 3600)),
        "savetime": now,
        "msg": {
            "diaObject": dia_object,
            "diaSource": dia_source,
            "prvDiaSources": prv_sources,
            "prvDiaForcedSources": prv_forced,
            "cutoutScience": None,
            "cutoutTemplate": None,
            "cutoutDifference": None,
        }
    }


def seed_mongodb(
    host: str,
    dbname: str,
    collection_name: str,
    username: str,
    password: str,
    num_objects: int,
    base_object_id: int = 90000000,
    clear_existing: bool = False,
):
    """Seed MongoDB with test alert documents."""

    # Connect to MongoDB
    connstr = f"mongodb://{username}:{password}@{host}:27017/?authSource={dbname}"
    client = MongoClient(connstr)
    db = client[dbname]
    collection = db[collection_name]

    if clear_existing:
        print(f"Clearing existing documents from {collection_name}...")
        result = collection.delete_many({})
        print(f"Deleted {result.deleted_count} documents")

    print(f"Generating {num_objects} alert documents...")

    documents = []
    source_id = base_object_id * 10
    forced_id = base_object_id * 100

    for i in range(num_objects):
        object_id = base_object_id + i

        # Random sky position
        ra = random.uniform(0, 360)
        dec = random.uniform(-90, 90)

        # Random MJD (around current time)
        mjd = 60000 + random.uniform(0, 100)

        doc = generate_alert_document(
            object_id=object_id,
            source_id=source_id,
            forced_source_id_start=forced_id,
            ra=ra,
            dec=dec,
            mjd=mjd,
            num_prv_sources=random.randint(1, 5),
            num_prv_forced=random.randint(3, 10),
        )
        documents.append(doc)

        source_id += 10  # Leave room for prv sources
        forced_id += 20  # Leave room for prv forced sources

    print(f"Inserting {len(documents)} documents into {dbname}.{collection_name}...")
    result = collection.insert_many(documents)
    print(f"Inserted {len(result.inserted_ids)} documents")

    # Print summary
    print("\nSummary:")
    print(f"  Collection: {dbname}.{collection_name}")
    print(f"  Documents inserted: {len(result.inserted_ids)}")
    print(f"  Object ID range: {base_object_id} - {base_object_id + num_objects - 1}")
    print(f"\nTo import these into PostgreSQL, run:")
    print(f"  python -m services.source_importer -p realtime -c {collection_name}")

    return len(result.inserted_ids)


def main():
    parser = argparse.ArgumentParser(
        description="Seed MongoDB with test alert documents for source importer testing",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-H", "--host",
        default="mongodb",
        help="MongoDB host",
    )
    parser.add_argument(
        "-d", "--dbname",
        default="brokeralert",
        help="MongoDB database name",
    )
    parser.add_argument(
        "-c", "--collection",
        default="test_alerts",
        help="MongoDB collection name to seed",
    )
    parser.add_argument(
        "-u", "--username",
        default="alertwriter",
        help="MongoDB username",
    )
    parser.add_argument(
        "-p", "--password",
        default="writer",
        help="MongoDB password",
    )
    parser.add_argument(
        "-n", "--num-objects",
        type=int,
        default=10,
        help="Number of objects (alerts) to generate",
    )
    parser.add_argument(
        "--base-object-id",
        type=int,
        default=90000000,
        help="Starting object ID (to avoid conflicts with existing data)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing documents in the collection before seeding",
    )

    args = parser.parse_args()

    try:
        seed_mongodb(
            host=args.host,
            dbname=args.dbname,
            collection_name=args.collection,
            username=args.username,
            password=args.password,
            num_objects=args.num_objects,
            base_object_id=args.base_object_id,
            clear_existing=args.clear,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
