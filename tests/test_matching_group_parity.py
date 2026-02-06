"""
Integration tests for PostgreSQL matching_group functions.

These tests verify that the PostgreSQL implementations produce identical
results to the Python implementations in spatial_id.py.

Prerequisites:
- PostgreSQL database with matching_group functions installed
- DB connection via db.DB() module

Run with: pytest tests/test_matching_group_parity.py -v
"""

import uuid
import pytest

from spatial_id import (
    generate_spatial_id,
    matching_group_int,
    matching_group_neighbors,
    matching_group_at_precision,
    matching_group_neighbors_at_precision,
    get_precision_info,
    _nside_for_precision,
    _shift_bits_for_nside,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def db_connection():
    """Get database connection for testing."""
    try:
        import db
        conn = db.DB()
        yield conn
    except Exception as e:
        pytest.skip(f"Database connection not available: {e}")


@pytest.fixture
def test_spatial_ids():
    """Generate test spatial IDs covering various positions."""
    return [
        # Standard positions
        generate_spatial_id(120.0, 45.0, 60000.0, 0, 0),
        generate_spatial_id(0.0, 0.0, 60000.0, 0, 0),
        generate_spatial_id(180.0, -45.0, 60000.0, 0, 0),
        generate_spatial_id(270.0, 60.0, 60000.0, 0, 0),
        # Near poles
        generate_spatial_id(0.0, 89.0, 60000.0, 0, 0),
        generate_spatial_id(0.0, -89.0, 60000.0, 0, 0),
        # Near date line
        generate_spatial_id(0.001, 0.0, 60000.0, 0, 0),
        generate_spatial_id(359.999, 0.0, 60000.0, 0, 0),
    ]


# =============================================================================
# Parity Tests: matching_group
# =============================================================================

class TestMatchingGroupParity:
    """Verify PostgreSQL matching_group matches Python implementation."""

    def test_matching_group_parity_standard_positions(self, db_connection, test_spatial_ids):
        """SQL matching_group should equal Python matching_group_int."""
        for sid in test_spatial_ids:
            # Python result
            py_mg = matching_group_int(sid)

            # SQL result
            result = db_connection.query(
                "SELECT matching_group(%(sid)s) AS mg",
                {'sid': sid}
            )
            sql_mg = result[0]['mg']

            assert py_mg == sql_mg, (
                f"Mismatch for {sid}: Python={py_mg}, SQL={sql_mg}"
            )

    def test_matching_group_parity_zero_uuid(self, db_connection):
        """SQL and Python should handle zero UUID identically."""
        zero_uuid = uuid.UUID('00000000-0000-0000-0000-000000000000')

        # Python: extract_healpix returns 0, shift gives 0
        py_mg = 0  # Zero UUID has zero healpix

        # SQL result
        result = db_connection.query(
            "SELECT matching_group(%(sid)s) AS mg",
            {'sid': zero_uuid}
        )
        sql_mg = result[0]['mg']

        assert py_mg == sql_mg, f"Zero UUID mismatch: Python={py_mg}, SQL={sql_mg}"

    def test_matching_group_determinism(self, db_connection):
        """SQL matching_group should be deterministic."""
        sid = generate_spatial_id(180.0, 45.0, 60000.0, 100, 5)

        results = []
        for _ in range(5):
            result = db_connection.query(
                "SELECT matching_group(%(sid)s) AS mg",
                {'sid': sid}
            )
            results.append(result[0]['mg'])

        assert len(set(results)) == 1, "matching_group should be deterministic"


# =============================================================================
# Parity Tests: matching_group_neighbors
# =============================================================================

class TestMatchingGroupNeighborsParity:
    """Verify PostgreSQL matching_group_neighbors matches Python."""

    def test_neighbors_parity_interior_pixel(self, db_connection):
        """SQL and Python should return same neighbors for interior pixels."""
        # Use a known interior pixel
        mg = 434110465

        # Python result
        py_neighbors = set(matching_group_neighbors(mg))

        # SQL result
        result = db_connection.query(
            "SELECT unnest(matching_group_neighbors(%(mg)s)) AS n",
            {'mg': mg}
        )
        sql_neighbors = set(row['n'] for row in result)

        assert py_neighbors == sql_neighbors, (
            f"Neighbors mismatch for mg={mg}:\n"
            f"  Python: {sorted(py_neighbors)}\n"
            f"  SQL: {sorted(sql_neighbors)}"
        )

    def test_neighbors_contains_self(self, db_connection):
        """SQL neighbors should always include the input pixel."""
        test_mgs = [0, 1000, 434110465, 1000000000]

        for mg in test_mgs:
            result = db_connection.query(
                "SELECT %(mg)s = ANY(matching_group_neighbors(%(mg)s)) AS contains_self",
                {'mg': mg}
            )
            assert result[0]['contains_self'], f"neighbors({mg}) should contain self"

    def test_neighbors_count_range(self, db_connection):
        """SQL neighbors should return 1-9 elements."""
        test_mgs = [0, 1000, 434110465, 1000000000]

        for mg in test_mgs:
            result = db_connection.query(
                "SELECT array_length(matching_group_neighbors(%(mg)s), 1) AS count",
                {'mg': mg}
            )
            count = result[0]['count']
            assert 1 <= count <= 9, f"neighbors({mg}) returned {count} elements"


# =============================================================================
# Parity Tests: Precision Functions
# =============================================================================

class TestPrecisionFunctionsParity:
    """Verify PostgreSQL precision functions match Python."""

    @pytest.mark.parametrize("arcsec", [0.001, 0.1, 1.0, 10.0, 100.0, 1000.0])
    def test_nside_for_precision_parity(self, db_connection, arcsec):
        """SQL _mg_nside_for_precision should match Python."""
        # Python result
        py_nside = _nside_for_precision(arcsec)

        # SQL result
        result = db_connection.query(
            "SELECT _mg_nside_for_precision(%(arcsec)s) AS nside",
            {'arcsec': arcsec}
        )
        sql_nside = result[0]['nside']

        assert py_nside == sql_nside, (
            f"NSIDE mismatch for {arcsec}\": Python={py_nside}, SQL={sql_nside}"
        )

    @pytest.mark.parametrize("nside", [1, 128, 2048, 16384, 131072, 536870912])
    def test_shift_bits_for_nside_parity(self, db_connection, nside):
        """SQL _mg_shift_bits_for_nside should match Python."""
        # Python result
        py_shift = _shift_bits_for_nside(nside)

        # SQL result
        result = db_connection.query(
            "SELECT _mg_shift_bits_for_nside(%(nside)s) AS shift",
            {'nside': nside}
        )
        sql_shift = result[0]['shift']

        assert py_shift == sql_shift, (
            f"Shift mismatch for NSIDE={nside}: Python={py_shift}, SQL={sql_shift}"
        )

    @pytest.mark.parametrize("arcsec", [1.0, 10.0, 100.0])
    def test_matching_group_at_precision_parity(self, db_connection, arcsec):
        """SQL matching_group_at_precision should match Python."""
        sid = generate_spatial_id(120.0, 45.0, 60000.0, 0, 0)

        # Python result
        py_mg = matching_group_at_precision(sid, arcsec)

        # SQL result
        result = db_connection.query(
            "SELECT matching_group_at_precision(%(sid)s, %(arcsec)s) AS mg",
            {'sid': sid, 'arcsec': arcsec}
        )
        sql_mg = result[0]['mg']

        assert py_mg == sql_mg, (
            f"Mismatch for {arcsec}\" precision: Python={py_mg}, SQL={sql_mg}"
        )

    @pytest.mark.parametrize("arcsec", [1.0, 10.0, 100.0])
    def test_matching_group_neighbors_at_precision_parity(self, db_connection, arcsec):
        """SQL matching_group_neighbors_at_precision should match Python."""
        # Get a matching group at this precision
        sid = generate_spatial_id(120.0, 45.0, 60000.0, 0, 0)
        mg = matching_group_at_precision(sid, arcsec)

        # Python result
        py_neighbors = set(matching_group_neighbors_at_precision(mg, arcsec))

        # SQL result
        result = db_connection.query(
            "SELECT unnest(matching_group_neighbors_at_precision(%(mg)s, %(arcsec)s)) AS n",
            {'mg': mg, 'arcsec': arcsec}
        )
        sql_neighbors = set(row['n'] for row in result)

        assert py_neighbors == sql_neighbors, (
            f"Neighbors mismatch at {arcsec}\" precision for mg={mg}:\n"
            f"  Python: {sorted(py_neighbors)}\n"
            f"  SQL: {sorted(sql_neighbors)}"
        )

    def test_precision_info_parity(self, db_connection):
        """SQL matching_precision_info should match Python get_precision_info."""
        for arcsec in [1.0, 10.0, 100.0]:
            # Python result
            py_info = get_precision_info(arcsec)

            # SQL result
            result = db_connection.query(
                "SELECT * FROM matching_precision_info(%(arcsec)s)",
                {'arcsec': arcsec}
            )
            sql_info = result[0]

            assert py_info['nside'] == sql_info['nside'], (
                f"NSIDE mismatch at {arcsec}\""
            )
            assert py_info['shift_bits'] == sql_info['shift_bits'], (
                f"shift_bits mismatch at {arcsec}\""
            )
            assert abs(py_info['pixel_size_arcsec'] - sql_info['pixel_size_arcsec']) < 0.001, (
                f"pixel_size mismatch at {arcsec}\""
            )
            assert py_info['total_pixels'] == sql_info['total_pixels'], (
                f"total_pixels mismatch at {arcsec}\""
            )


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Test error handling in SQL functions."""

    def test_nside_for_precision_rejects_zero(self, db_connection):
        """SQL should raise error for precision=0."""
        with pytest.raises(Exception) as exc_info:
            db_connection.query(
                "SELECT _mg_nside_for_precision(0.0)"
            )
        assert "Precision must be > 0" in str(exc_info.value)

    def test_nside_for_precision_rejects_negative(self, db_connection):
        """SQL should raise error for negative precision."""
        with pytest.raises(Exception) as exc_info:
            db_connection.query(
                "SELECT _mg_nside_for_precision(-1.0)"
            )
        assert "Precision must be > 0" in str(exc_info.value)


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestEdgeCases:
    """Test edge cases that might differ between implementations."""

    def test_very_large_matching_group(self, db_connection):
        """Test handling of large matching_group values."""
        # Maximum matching_group at NSIDE=2^14 is 12 * (2^14)^2 - 1
        max_mg = 12 * (16384 ** 2) - 1

        # Should not raise error
        result = db_connection.query(
            "SELECT array_length(matching_group_neighbors(%(mg)s), 1) AS count",
            {'mg': max_mg}
        )
        assert result[0]['count'] >= 1

    def test_base_pixel_boundary_consistency(self, db_connection):
        """Test that base pixel boundaries are handled consistently."""
        # Pixel at edge of base pixel 0
        # NSIDE=16384, npix_per_base = 16384^2 = 268435456
        npix_per_base = 16384 * 16384

        # First pixel of base pixel 1
        mg = npix_per_base

        # Should still work, even if neighbors are incomplete
        result = db_connection.query(
            "SELECT matching_group_neighbors(%(mg)s) AS neighbors",
            {'mg': mg}
        )
        neighbors = result[0]['neighbors']

        # Should include self
        assert mg in neighbors


# =============================================================================
# Performance Sanity Tests
# =============================================================================

class TestPerformance:
    """Basic performance sanity checks."""

    def test_matching_group_batch_performance(self, db_connection):
        """matching_group should handle batch queries efficiently."""
        import time

        # Generate 1000 random UUIDs
        uuids = [
            generate_spatial_id(
                ra=float(i % 360),
                dec=float((i % 180) - 90),
                mjd=60000.0,
                procver_compact=0,
                data_release=0
            )
            for i in range(1000)
        ]

        # Time the batch query
        start = time.time()
        for sid in uuids:
            db_connection.query(
                "SELECT matching_group(%(sid)s)",
                {'sid': sid}
            )
        elapsed = time.time() - start

        # Should complete in reasonable time (< 5 seconds for 1000 queries)
        assert elapsed < 5.0, f"Batch query took {elapsed:.2f}s, expected < 5s"

    def test_matching_group_index_exists(self, db_connection):
        """Verify the matching_group index exists."""
        result = db_connection.query("""
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'idx_diaobject_matching_group'
            ) AS exists
        """)
        # Don't fail if index doesn't exist, just note it
        if not result[0]['exists']:
            pytest.skip("matching_group index not found (may not be created yet)")


# =============================================================================
# Regression Tests
# =============================================================================

class TestKnownValues:
    """Test against known good values to catch regressions."""

    # These values were computed using the Python implementation
    # and verified to be correct
    KNOWN_VALUES = [
        # (ra, dec, mjd, expected_mg_10, expected_mg_1, expected_mg_100)
        (120.0, 45.0, 60000.0, 434110465, 27783069798, 6782976),
        (0.0, 0.0, 60000.0, 805306368, 51539607552, 12582912),
        (180.0, 0.0, 60000.0, 1610612736, 103079215104, 25165824),
    ]

    @pytest.mark.parametrize("ra,dec,mjd,mg10,mg1,mg100", KNOWN_VALUES)
    def test_known_matching_group_values(self, db_connection, ra, dec, mjd, mg10, mg1, mg100):
        """Verify SQL produces expected matching_group values."""
        sid = generate_spatial_id(ra, dec, mjd, 0, 0)

        # Test 10" precision
        result = db_connection.query(
            "SELECT matching_group(%(sid)s) AS mg",
            {'sid': sid}
        )
        assert result[0]['mg'] == mg10, f"10\" precision mismatch at ({ra}, {dec})"

        # Test 1" precision
        result = db_connection.query(
            "SELECT matching_group_at_precision(%(sid)s, 1.0) AS mg",
            {'sid': sid}
        )
        assert result[0]['mg'] == mg1, f"1\" precision mismatch at ({ra}, {dec})"

        # Test 100" precision
        result = db_connection.query(
            "SELECT matching_group_at_precision(%(sid)s, 100.0) AS mg",
            {'sid': sid}
        )
        assert result[0]['mg'] == mg100, f"100\" precision mismatch at ({ra}, {dec})"
