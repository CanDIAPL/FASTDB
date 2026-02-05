#!/usr/bin/env python
"""
Verify multi-writer import results.

Checks:
1. No duplicate spatial_ids in database
2. All spatial_ids are deterministic (regenerating produces same result)
3. spatial_group() works correctly
4. Import statistics match expectations
"""

import sys
import os
import argparse
from collections import defaultdict

# Add FASTDB directories to path for imports
# Works both locally (src/) and in container (/fastdb/)
_base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(_base, 'src'))
sys.path.insert(0, '/fastdb')

import db
from spatial_id import generate_spatial_id, spatial_group_int, extract_all


class VerificationResult:
    """Container for verification results."""

    def __init__(self):
        self.checks = []
        self.passed = True

    def add_check(self, name, passed, message=""):
        self.checks.append({
            'name': name,
            'passed': passed,
            'message': message
        })
        if not passed:
            self.passed = False

    def print_summary(self):
        print("\n" + "=" * 60)
        print("VERIFICATION RESULTS")
        print("=" * 60)

        for check in self.checks:
            status = "PASS" if check['passed'] else "FAIL"
            print(f"[{status}] {check['name']}")
            if check['message']:
                print(f"       {check['message']}")

        print()
        if self.passed:
            print("ALL CHECKS PASSED")
        else:
            print("SOME CHECKS FAILED")
        print("=" * 60)


def check_no_duplicate_spatial_ids(conn):
    """Verify no duplicate spatial_ids exist in diaobject table."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT spatial_id, COUNT(*) as cnt
        FROM diaobject
        GROUP BY spatial_id
        HAVING COUNT(*) > 1
    """)
    duplicates = cursor.fetchall()

    if duplicates:
        return False, f"Found {len(duplicates)} duplicate spatial_ids"
    return True, "No duplicate spatial_ids found"


def check_deterministic_spatial_ids(conn, procver=0, data_release=0):
    """Verify spatial_ids can be regenerated deterministically.

    Sample objects and verify their spatial_ids match what generate_spatial_id produces.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT diaobjectid, ra, dec, validitystartmjdtai, spatial_id
        FROM diaobject
        LIMIT 100
    """)
    rows = cursor.fetchall()

    if not rows:
        return True, "No objects to verify (empty table)"

    mismatches = []
    for diaobjectid, ra, dec, mjd, stored_sid in rows:
        expected_sid = generate_spatial_id(ra, dec, mjd, procver, data_release)
        if str(expected_sid) != str(stored_sid):
            mismatches.append({
                'diaobjectid': diaobjectid,
                'stored': stored_sid,
                'expected': expected_sid
            })

    if mismatches:
        return False, f"{len(mismatches)} spatial_ids don't match regenerated values"
    return True, f"All {len(rows)} sampled spatial_ids are deterministic"


def check_spatial_grouping(conn):
    """Verify spatial_group_int groups objects at same position.

    Objects with same (ra, dec) but different mjd should have same spatial_group.
    """
    cursor = conn.cursor()

    # Find objects with multiple spatial_ids (multiple observations)
    cursor.execute("""
        SELECT ra, dec, array_agg(spatial_id) as spatial_ids
        FROM diaobject
        GROUP BY ra, dec
        HAVING COUNT(*) > 1
        LIMIT 10
    """)
    rows = cursor.fetchall()

    if not rows:
        return True, "No multi-observation objects to verify"

    mismatches = 0
    for ra, dec, spatial_ids in rows:
        groups = set()
        for sid_str in spatial_ids:
            import uuid
            sid = uuid.UUID(str(sid_str))
            groups.add(spatial_group_int(sid))

        if len(groups) > 1:
            mismatches += 1

    if mismatches:
        return False, f"{mismatches} positions have inconsistent spatial groups"
    return True, f"All {len(rows)} sampled multi-observation positions have consistent spatial groups"


def check_object_counts(conn, expected_objects=None, expected_rootids=None):
    """Verify object counts match expectations."""
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM diaobject")
    actual_objects = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT spatial_id) FROM diaobject")
    actual_rootids = cursor.fetchone()[0]

    messages = [f"Objects: {actual_objects}, Unique spatial_ids: {actual_rootids}"]

    if expected_objects is not None and actual_objects != expected_objects:
        return False, f"Expected {expected_objects} objects, got {actual_objects}"

    if expected_rootids is not None and actual_rootids != expected_rootids:
        return False, f"Expected {expected_rootids} spatial_ids, got {actual_rootids}"

    return True, messages[0]


def check_root_diaobject_consistency(conn):
    """Verify all rootids in diaobject exist in root_diaobject."""
    cursor = conn.cursor()

    # Check if root_diaobject table exists
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'root_diaobject'
        )
    """)
    if not cursor.fetchone()[0]:
        return True, "root_diaobject table does not exist (skipping check)"

    cursor.execute("""
        SELECT COUNT(*)
        FROM diaobject o
        LEFT JOIN root_diaobject r ON o.rootid = r.id
        WHERE r.id IS NULL
    """)
    orphans = cursor.fetchone()[0]

    if orphans > 0:
        return False, f"{orphans} diaobjects have rootids not in root_diaobject"
    return True, "All rootids exist in root_diaobject"


def check_source_counts(conn):
    """Report on source counts."""
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM diasource")
    source_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM diaforcedsource")
    forced_count = cursor.fetchone()[0]

    return True, f"Sources: {source_count}, Forced sources: {forced_count}"


def verify_all(conn, expected_objects=None, expected_rootids=None, procver=0, data_release=0):
    """Run all verification checks."""
    result = VerificationResult()

    # Check 1: No duplicate spatial_ids
    passed, msg = check_no_duplicate_spatial_ids(conn)
    result.add_check("No duplicate spatial_ids", passed, msg)

    # Check 2: Deterministic spatial_ids
    passed, msg = check_deterministic_spatial_ids(conn, procver, data_release)
    result.add_check("Deterministic spatial_id generation", passed, msg)

    # Check 3: Spatial grouping consistency
    passed, msg = check_spatial_grouping(conn)
    result.add_check("Spatial grouping consistency", passed, msg)

    # Check 4: Object counts
    passed, msg = check_object_counts(conn, expected_objects, expected_rootids)
    result.add_check("Object counts", passed, msg)

    # Check 5: root_diaobject consistency
    passed, msg = check_root_diaobject_consistency(conn)
    result.add_check("root_diaobject consistency", passed, msg)

    # Check 6: Source counts (informational)
    passed, msg = check_source_counts(conn)
    result.add_check("Source counts (info)", passed, msg)

    return result


def main():
    parser = argparse.ArgumentParser(
        description='Verify multi-writer import results'
    )
    parser.add_argument('--expected-objects', type=int,
                        help='Expected number of objects')
    parser.add_argument('--expected-rootids', type=int,
                        help='Expected number of unique rootids')
    parser.add_argument('--procver', type=int, default=0,
                        help='Processing version used during import')
    parser.add_argument('--data-release', type=int, default=0,
                        help='Data release used during import')
    args = parser.parse_args()

    print("Running verification checks...")

    with db.DB() as conn:
        result = verify_all(
            conn,
            expected_objects=args.expected_objects,
            expected_rootids=args.expected_rootids,
            procver=args.procver,
            data_release=args.data_release
        )

    result.print_summary()

    return 0 if result.passed else 1


if __name__ == '__main__':
    sys.exit(main())
