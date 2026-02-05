#!/usr/bin/env python
"""
Multi-Writer Source Importer Demo.

Demonstrates that multiple source_importer instances can run concurrently
without conflicts, thanks to deterministic spatial_id generation.

This script:
1. Cleans previous test data (MongoDB + PostgreSQL)
2. Ensures base_processing_version exists
3. Generates test data with overlapping partitions
4. Launches N concurrent source_importer instances
5. Waits for completion
6. Runs verification
7. Prints summary
"""

import sys
import os
import time
import argparse
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add FASTDB directories to path for imports
# Works both locally (src/) and in container (/fastdb/)
_base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(_base, 'src'))
sys.path.insert(0, os.path.join(_base, 'src', 'services'))
# Container paths
sys.path.insert(0, '/fastdb')
sys.path.insert(0, '/fastdb/services')

import db
from source_importer import SourceImporter
from generate_test_data import generate_test_objects, generate_partitioned_data
from verify_multiwriter import verify_all


# Demo configuration
DEMO_COLLECTION = 'multiwriter_test'
DEMO_PROCVER_DESC = 'multiwriter_demo_test'
DEMO_PROCVER = 0
DEMO_DATA_RELEASE = 0


class ImporterThread:
    """Wrapper to run source_importer in a thread with statistics."""

    def __init__(self, thread_id, collection_name, base_procver_id, t0, t1):
        self.thread_id = thread_id
        self.collection_name = collection_name
        self.base_procver_id = base_procver_id
        self.t0 = t0
        self.t1 = t1
        self.result = None
        self.error = None
        self.duration = 0

    def run(self):
        """Execute the import for a specific time range."""
        start = time.time()
        try:
            si = SourceImporter(
                self.base_procver_id,
                self.base_procver_id,
                procver=DEMO_PROCVER,
                data_release=DEMO_DATA_RELEASE
            )

            # Import using time-bounded import for this partition
            with db.MG() as mgclient:
                collection = db.get_mongo_collection(mgclient, self.collection_name)

                with db.DB() as pqconn:
                    # Import objects in our time window
                    nobj, nroot = si.import_objects_from_collection(
                        collection, t0=self.t0, t1=self.t1, conn=pqconn, commit=False
                    )
                    nsrc = si.import_sources_from_collection(
                        collection, t0=self.t0, t1=self.t1, conn=pqconn, commit=False
                    )
                    nprvsrc = si.import_prvsources_from_collection(
                        collection, t0=self.t0, t1=self.t1, conn=pqconn, commit=False
                    )
                    nprvfrc = si.import_prvforcedsources_from_collection(
                        collection, t0=self.t0, t1=self.t1, conn=pqconn, commit=False
                    )
                    pqconn.commit()

                    self.result = (nobj, nroot, nsrc + nprvsrc, nprvfrc)

        except Exception as e:
            self.error = str(e)
            import traceback
            traceback.print_exc()
        finally:
            self.duration = time.time() - start


def clean_test_data(collection_name):
    """Remove test data from both MongoDB and PostgreSQL."""
    print("Cleaning previous test data...")

    # Clean MongoDB
    with db.MG() as mgclient:
        collection = db.get_mongo_collection(mgclient, collection_name)
        collection.drop()
        print(f"  Dropped MongoDB collection: {collection_name}")

    # Clean PostgreSQL (in correct order for foreign keys)
    # Handle missing tables gracefully
    with db.DB() as conn:
        cursor = conn.cursor()

        tables_to_clean = [
            ('diaforcedsource', None),
            ('diasource', None),
            ('diaobject', None),
            ('root_diaobject', None),
            ('diasource_import_time', f"collection = '{collection_name}'"),
        ]

        for table, condition in tables_to_clean:
            try:
                # Check if table exists first to avoid transaction abort
                cursor.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                    (table,)
                )
                if not cursor.fetchone()[0]:
                    print(f"  Table {table} does not exist (skipping)")
                    continue

                if condition:
                    cursor.execute(f"DELETE FROM {table} WHERE {condition}")
                else:
                    cursor.execute(f"DELETE FROM {table}")
                print(f"  Deleted {cursor.rowcount} rows from {table}")
            except Exception as e:
                conn.rollback()
                print(f"  Error cleaning {table}: {e}")

        conn.commit()

    print()


def ensure_processing_version():
    """Ensure the demo processing version exists."""
    print("Ensuring processing version exists...")

    with db.DB() as conn:
        cursor = conn.cursor()

        # Check if it exists
        cursor.execute(
            "SELECT id FROM base_processing_version WHERE description = %s",
            (DEMO_PROCVER_DESC,)
        )
        row = cursor.fetchone()

        if row:
            procver_id = row[0]
            print(f"  Using existing: {procver_id}")
        else:
            # Create new
            procver_id = uuid.uuid4()
            cursor.execute(
                "INSERT INTO base_processing_version (id, description) VALUES (%s, %s)",
                (str(procver_id), DEMO_PROCVER_DESC)
            )
            conn.commit()
            print(f"  Created new: {procver_id}")

    print()
    return procver_id


def generate_and_insert_data(n_objects, detections_per_object, n_partitions, overlap):
    """Generate test data and insert into MongoDB."""
    print("Generating test data...")
    print(f"  Objects: {n_objects}")
    print(f"  Detections per object: {detections_per_object}")
    print(f"  Partitions: {n_partitions}")
    print(f"  Overlap: {overlap*100:.0f}%")

    # Generate objects
    objects = generate_test_objects(n_objects)

    # Generate partitioned data with time boundaries
    partitions, time_boundaries = generate_partitioned_data(
        objects,
        detections_per_object=detections_per_object,
        n_partitions=n_partitions,
        overlap_fraction=overlap
    )

    # Insert into MongoDB
    print("\nInserting into MongoDB...")
    with db.MG() as mgclient:
        collection = db.get_mongo_collection(mgclient, DEMO_COLLECTION)
        collection.drop()

        total_docs = 0
        for p, docs in enumerate(partitions):
            if docs:
                collection.insert_many(docs)
                total_docs += len(docs)
                t0, t1 = time_boundaries[p]
                print(f"  Partition {p}: {len(docs)} documents (time: {t0.isoformat()} - {t1.isoformat()})")

    print(f"\nTotal documents inserted: {total_docs}")
    print()

    return objects, partitions, time_boundaries


def run_concurrent_imports(n_threads, procver_id, time_boundaries):
    """Run multiple source_importer instances concurrently.

    Each thread processes a different time window from the same collection.
    Overlapping data (same objects appearing in multiple time windows)
    demonstrates conflict-free concurrent imports.
    """
    print(f"Launching {n_threads} concurrent importers...")
    print()

    importers = []
    for i in range(n_threads):
        t0, t1 = time_boundaries[i]
        imp = ImporterThread(
            thread_id=i,
            collection_name=DEMO_COLLECTION,
            base_procver_id=procver_id,
            t0=t0,
            t1=t1
        )
        importers.append(imp)

    # Run all importers concurrently
    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        futures = {executor.submit(imp.run): imp for imp in importers}

        for future in as_completed(futures):
            imp = futures[future]
            if imp.error:
                print(f"  Thread {imp.thread_id}: ERROR - {imp.error}")
            else:
                nobj, nroot, nsrc, nfrc = imp.result
                print(f"  Thread {imp.thread_id}: {nobj} objects, {nroot} roots, "
                      f"{nsrc} sources, {nfrc} forced ({imp.duration:.2f}s)")

    print()

    # Check for errors
    errors = [imp for imp in importers if imp.error]
    return importers, errors


def run_verification(n_objects, detections_per_object, n_partitions, overlap):
    """Run verification checks."""
    print("Running verification...")
    print()

    # Calculate expected counts
    # - Overlap objects appear in all partitions with same diaObjectId -> same rootid
    # - Unique objects are split across partitions
    # - MongoDB groups by diaObjectId, so each object = 1 entry in diaobject table
    n_overlap = int(n_objects * overlap)
    n_unique_per_partition = (n_objects - n_overlap) // n_partitions
    n_unique_total = n_unique_per_partition * n_partitions

    # Total unique objects = overlap + unique (each diaObjectId = 1 object)
    expected_objects = n_overlap + n_unique_total

    # Each unique diaObjectId gets ONE rootid (based on its validityStartMjdTai)
    # Multiple detections of same object don't create multiple rootids
    expected_rootids = expected_objects

    print(f"Expected: {expected_objects} objects (overlap: {n_overlap}, unique: {n_unique_total})")
    print(f"Expected: {expected_rootids} unique rootids (1 per object)")
    print()

    with db.DB() as conn:
        result = verify_all(
            conn,
            expected_objects=expected_objects,
            expected_rootids=expected_rootids,
            procver=DEMO_PROCVER,
            data_release=DEMO_DATA_RELEASE
        )

    result.print_summary()
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Multi-Writer Source Importer Demo'
    )
    parser.add_argument('-n', '--num-objects', type=int, default=100,
                        help='Number of unique objects')
    parser.add_argument('-d', '--detections', type=int, default=3,
                        help='Detections per object')
    parser.add_argument('-p', '--partitions', type=int, default=3,
                        help='Number of concurrent importers (partitions)')
    parser.add_argument('-o', '--overlap', type=float, default=0.3,
                        help='Overlap fraction (0.3 = 30%%)')
    parser.add_argument('--skip-clean', action='store_true',
                        help='Skip cleaning previous data')
    parser.add_argument('--skip-generate', action='store_true',
                        help='Skip data generation (use existing)')
    args = parser.parse_args()

    print("=" * 60)
    print("MULTI-WRITER SOURCE IMPORTER DEMO")
    print("=" * 60)
    print()

    # Step 1: Clean previous data
    if not args.skip_clean:
        clean_test_data(DEMO_COLLECTION)

    # Step 2: Ensure processing version exists
    procver_id = ensure_processing_version()

    # Step 3: Generate and insert test data
    time_boundaries = None
    if not args.skip_generate:
        objects, partitions, time_boundaries = generate_and_insert_data(
            args.num_objects,
            args.detections,
            args.partitions,
            args.overlap
        )
    else:
        # If skipping generate, we need to create dummy time boundaries
        import datetime
        base_time = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
        time_boundaries = [
            (base_time + datetime.timedelta(hours=i) - datetime.timedelta(seconds=1),
             base_time + datetime.timedelta(hours=i+1))
            for i in range(args.partitions)
        ]

    # Step 4: Run concurrent importers
    print("=" * 60)
    print("CONCURRENT IMPORT PHASE")
    print("=" * 60)
    print()

    start_time = time.time()
    importers, errors = run_concurrent_imports(args.partitions, procver_id, time_boundaries)
    total_time = time.time() - start_time

    print(f"Total import time: {total_time:.2f}s")
    print()

    if errors:
        print(f"WARNING: {len(errors)} importers failed!")
        for imp in errors:
            print(f"  Thread {imp.thread_id}: {imp.error}")
        print()

    # Step 5: Run verification
    print("=" * 60)
    print("VERIFICATION PHASE")
    print("=" * 60)
    print()

    result = run_verification(args.num_objects, args.detections, args.partitions, args.overlap)

    # Final summary
    print()
    print("=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)

    if result.passed and not errors:
        print()
        print("SUCCESS: Multi-writer import completed without conflicts!")
        print("The deterministic spatial_id generation ensures that:")
        print("  - Same data imported by different instances produces identical rootids")
        print("  - ON CONFLICT DO NOTHING safely handles duplicates")
        print("  - No data corruption or duplicate entries occur")
        return 0
    else:
        print()
        print("FAILURE: Some checks did not pass.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
