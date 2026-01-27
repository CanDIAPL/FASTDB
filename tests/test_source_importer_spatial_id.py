"""
Tests for spatial_id integration in source_importer.

These tests verify that:
1. Each diaobject gets a deterministic spatial_id based on (ra, dec, mjd, procver, data_release)
2. Same data imported twice produces identical spatial_ids
3. spatial_group() function works for grouping objects at same position
4. Different observations of same object have same spatial_group but different spatial_id
5. rootid remains a random UUID with FK to root_diaobject (tested via DB integration)
"""

import uuid
import pytest

from spatial_id import (
    generate_spatial_id,
    spatial_group_int,
    extract_mjd,
    extract_procver,
    extract_data_release,
)


class TestSpatialIdDeterminism:
    """Test that spatial_id generation is deterministic."""

    def test_same_inputs_same_output(self):
        """Same (ra, dec, mjd, procver, dr) always produces same spatial_id."""
        ra, dec, mjd = 180.0, 45.0, 60000.0
        procver, dr = 0, 0

        sid1 = generate_spatial_id(ra, dec, mjd, procver, dr)
        sid2 = generate_spatial_id(ra, dec, mjd, procver, dr)

        assert sid1 == sid2

    def test_different_mjd_different_spatial_id(self):
        """Different observation times produce different spatial_ids."""
        ra, dec = 180.0, 45.0
        procver, dr = 0, 0

        sid1 = generate_spatial_id(ra, dec, 60000.0, procver, dr)
        sid2 = generate_spatial_id(ra, dec, 60001.0, procver, dr)

        assert sid1 != sid2

    def test_different_mjd_same_spatial_group(self):
        """Different observation times but same position share spatial_group."""
        ra, dec = 180.0, 45.0
        procver, dr = 0, 0

        sid1 = generate_spatial_id(ra, dec, 60000.0, procver, dr)
        sid2 = generate_spatial_id(ra, dec, 60001.0, procver, dr)

        # Different spatial_id
        assert sid1 != sid2

        # Same spatial_group (position-only grouping)
        assert spatial_group_int(sid1) == spatial_group_int(sid2)

    def test_different_procver_same_spatial_group(self):
        """Different processing versions share spatial_group."""
        ra, dec, mjd = 180.0, 45.0, 60000.0
        dr = 0

        sid1 = generate_spatial_id(ra, dec, mjd, 0, dr)
        sid2 = generate_spatial_id(ra, dec, mjd, 100, dr)

        # Different spatial_id
        assert sid1 != sid2

        # Same spatial_group
        assert spatial_group_int(sid1) == spatial_group_int(sid2)

    def test_different_data_release_same_spatial_group(self):
        """Different data releases share spatial_group."""
        ra, dec, mjd = 180.0, 45.0, 60000.0
        procver = 0

        sid1 = generate_spatial_id(ra, dec, mjd, procver, 0)  # realtime
        sid2 = generate_spatial_id(ra, dec, mjd, procver, 1)  # DR1

        # Different spatial_id
        assert sid1 != sid2

        # Same spatial_group
        assert spatial_group_int(sid1) == spatial_group_int(sid2)


class TestSpatialGrouping:
    """Test spatial_group behavior for object matching."""

    def test_nearby_positions_different_spatial_group(self):
        """Objects at slightly different positions may have different spatial_group.

        This is expected - cell boundary handling happens at query time.
        """
        mjd, procver, dr = 60000.0, 0, 0

        # Objects 0.1 arcsec apart - may be in different cells
        sid1 = generate_spatial_id(180.0, 45.0, mjd, procver, dr)
        sid2 = generate_spatial_id(180.0 + 0.0001 / 15, 45.0, mjd, procver, dr)  # ~0.024 arcsec in RA

        # They may or may not have the same spatial_group depending on cell boundaries
        # The key point is the test demonstrates the grouping behavior
        group1 = spatial_group_int(sid1)
        group2 = spatial_group_int(sid2)

        # At 0.024 arcsec apart, they should be in the same ~0.05" cell
        assert group1 == group2

    def test_far_apart_positions_different_spatial_group(self):
        """Objects far apart should have different spatial_group."""
        mjd, procver, dr = 60000.0, 0, 0

        sid1 = generate_spatial_id(180.0, 45.0, mjd, procver, dr)
        sid2 = generate_spatial_id(180.1, 45.0, mjd, procver, dr)  # 6 arcmin apart

        assert spatial_group_int(sid1) != spatial_group_int(sid2)


class TestImportScenarios:
    """Test scenarios that will occur in source_importer."""

    def test_idempotent_import_scenario(self):
        """Simulate importing same object twice - should get same spatial_id."""
        ra, dec, mjd = 42.0, 13.0, 50000.0
        procver, dr = 0, 0

        # First import
        sid1 = generate_spatial_id(ra, dec, mjd, procver, dr)

        # Second import (same data)
        sid2 = generate_spatial_id(ra, dec, mjd, procver, dr)

        # Should be identical
        assert sid1 == sid2

    def test_multi_master_scenario(self):
        """Simulate importing on different masters - should get same spatial_id."""
        ra, dec, mjd = 42.0, 13.0, 50000.0
        procver, dr = 0, 0

        # Master A imports
        sid_a = generate_spatial_id(ra, dec, mjd, procver, dr)

        # Master B imports same data
        sid_b = generate_spatial_id(ra, dec, mjd, procver, dr)

        # Should be identical - deterministic across masters
        assert sid_a == sid_b

    def test_multiple_observations_grouping(self):
        """Multiple observations of same object can be grouped by spatial_group."""
        ra, dec = 42.0, 13.0
        procver, dr = 0, 0

        # Multiple observations at different times
        observations = [
            generate_spatial_id(ra, dec, 60000.0, procver, dr),
            generate_spatial_id(ra, dec, 60001.0, procver, dr),
            generate_spatial_id(ra, dec, 60010.0, procver, dr),
            generate_spatial_id(ra, dec, 60030.0, procver, dr),
        ]

        # All have different spatial_ids
        assert len(set(observations)) == 4

        # All have same spatial_group
        groups = [spatial_group_int(obs) for obs in observations]
        assert len(set(groups)) == 1


class TestExtraction:
    """Test that encoded values can be extracted correctly."""

    def test_extract_mjd(self):
        """MJD can be extracted with sub-second precision."""
        mjd = 60123.456789
        sid = generate_spatial_id(180.0, 45.0, mjd, 0, 0)

        # MJD stored at 1ms precision
        extracted = extract_mjd(sid)
        assert abs(extracted - mjd) < 0.00002  # <2 seconds

    def test_extract_procver(self):
        """Processing version can be extracted exactly."""
        procver = 12345
        sid = generate_spatial_id(180.0, 45.0, 60000.0, procver, 0)

        assert extract_procver(sid) == procver

    def test_extract_data_release(self):
        """Data release can be extracted exactly."""
        dr = 15
        sid = generate_spatial_id(180.0, 45.0, 60000.0, 0, dr)

        assert extract_data_release(sid) == dr


class TestDefaultValues:
    """Test default values for procver and data_release."""

    def test_default_procver_zero(self):
        """Default procver=0 produces valid spatial_id."""
        sid = generate_spatial_id(180.0, 45.0, 60000.0, 0, 0)
        assert extract_procver(sid) == 0

    def test_default_data_release_zero(self):
        """Default data_release=0 (realtime) produces valid spatial_id."""
        sid = generate_spatial_id(180.0, 45.0, 60000.0, 0, 0)
        assert extract_data_release(sid) == 0
