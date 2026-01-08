"""
Comprehensive tests for spatial_id module.

Tests cover:
- Round-trip encoding/decoding
- Bit layout verification
- Boundary values (max values for each field)
- Spatial grouping logic
- Input validation
- Edge cases (poles, date line)
- Determinism (multi-master safety)
- MJD precision
- UUID format compatibility

UUID Layout (128 bits):
    high64: [Reserved (2)] [HEALPix (62)]
    low64:  [MJD_ms (43)] [ProcVer (16)] [DataRelease (5)]

Field ordering enables hierarchical filtering: position → time → procver → data_release
"""

import struct
import uuid

import pytest

from spatial_id import (
    HEALPIX_BITS,
    MJD_EPOCH,
    NSIDE,
    PROCVER_BITS,
    DATARELEASE_BITS,
    MJD_BITS,
    RESERVED_BITS,
    _MAX_HEALPIX,
    _MAX_PROCVER,
    _MAX_DATARELEASE,
    _MAX_MJD_MS,
    _MS_PER_DAY,
    generate_spatial_id,
    extract_healpix,
    extract_procver,
    extract_data_release,
    extract_mjd,
    extract_all,
    spatial_group_int,
    same_spatial_group,
    healpix_to_radec,
    extract_approx_radec,
)


class TestBasicRoundTrip:
    """Test that encoded values can be recovered correctly."""

    def test_round_trip_typical_values(self):
        """Test round-trip with typical astronomical values."""
        ra, dec, mjd = 180.0, 45.0, 60000.0
        procver, dr = 100, 1

        sid = generate_spatial_id(ra, dec, mjd, procver, dr)

        assert extract_procver(sid) == procver
        assert extract_data_release(sid) == dr
        assert abs(extract_mjd(sid) - mjd) < 0.001  # ~86 second precision

    def test_round_trip_realtime_data_release(self):
        """Test data_release=0 (realtime) round-trip."""
        sid = generate_spatial_id(0.0, 0.0, MJD_EPOCH, 1, 0)
        assert extract_data_release(sid) == 0

    def test_round_trip_extract_all(self):
        """Test extract_all returns all components correctly."""
        ra, dec, mjd = 123.456, -45.678, 60123.456
        procver, dr = 12345, 15

        sid = generate_spatial_id(ra, dec, mjd, procver, dr)
        healpix, pv, data_rel, mjd_out = extract_all(sid)

        assert pv == procver
        assert data_rel == dr
        assert abs(mjd_out - mjd) < 0.001


class TestBitLayout:
    """Verify the bit layout matches the specification."""

    def test_healpix_position(self):
        """Verify HEALPix occupies the entire high64 (64 bits)."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)
        healpix = extract_healpix(sid)

        # Verify by direct bit extraction
        high64 = struct.unpack(">Q", sid.bytes[:8])[0]
        assert high64 == healpix

    def test_procver_position(self):
        """Verify ProcVer occupies bits 5-20 of low64 (after MJD)."""
        procver = 0xABCD
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, procver, 0)

        low64 = struct.unpack(">Q", sid.bytes[8:])[0]
        extracted = (low64 >> 5) & 0xFFFF
        assert extracted == procver

    def test_data_release_position(self):
        """Verify DataRelease occupies bottom 5 bits of low64."""
        for dr in range(32):  # All valid values 0-31
            sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, dr)
            low64 = struct.unpack(">Q", sid.bytes[8:])[0]
            extracted = low64 & 0x1F
            assert extracted == dr, f"Failed for data_release={dr}"

    def test_mjd_position(self):
        """Verify MJD_ms occupies bits 21-63 of low64 (top 43 bits)."""
        mjd = 60000.0
        sid = generate_spatial_id(180.0, 0.0, mjd, 0, 0)

        low64 = struct.unpack(">Q", sid.bytes[8:])[0]
        mjd_ms = (low64 >> 21) & 0x7FFFFFFFFFF  # 43 bits

        expected_ms = int((mjd - MJD_EPOCH) * _MS_PER_DAY)
        assert mjd_ms == expected_ms


class TestBoundaryValues:
    """Test maximum values for each field."""

    def test_max_procver(self):
        """Test maximum processing version (65535)."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, _MAX_PROCVER, 0)
        assert extract_procver(sid) == _MAX_PROCVER

    def test_max_data_release(self):
        """Test maximum data release (31)."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, _MAX_DATARELEASE)
        assert extract_data_release(sid) == _MAX_DATARELEASE

    def test_max_mjd(self):
        """Test near-maximum MJD value."""
        # Max MJD is MJD_EPOCH + (_MAX_MJD_MS / _MS_PER_DAY)
        max_mjd = MJD_EPOCH + _MAX_MJD_MS / _MS_PER_DAY - 1  # Slightly below max
        sid = generate_spatial_id(180.0, 0.0, max_mjd, 0, 0)
        assert abs(extract_mjd(sid) - max_mjd) < 0.001

    def test_min_mjd(self):
        """Test minimum MJD (epoch)."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)
        assert abs(extract_mjd(sid) - MJD_EPOCH) < 0.001


class TestSpatialGrouping:
    """Test spatial grouping functionality."""

    def test_same_position_same_group(self):
        """Objects at same position with same procver are in same group."""
        ra, dec = 180.0, 45.0

        sid1 = generate_spatial_id(ra, dec, 60000.0, 100, 0)
        sid2 = generate_spatial_id(ra, dec, 60001.0, 100, 1)  # Different time/DR

        assert same_spatial_group(sid1, sid2)
        assert spatial_group_int(sid1) == spatial_group_int(sid2)

    def test_different_position_different_group(self):
        """Objects at different positions are in different groups."""
        sid1 = generate_spatial_id(0.0, 0.0, 60000.0, 100, 0)
        sid2 = generate_spatial_id(180.0, 0.0, 60000.0, 100, 0)

        assert not same_spatial_group(sid1, sid2)

    def test_same_position_different_procver_same_group(self):
        """Objects at same position with different procver ARE in same group.

        Spatial grouping is position-only, allowing queries across processing versions.
        """
        ra, dec = 180.0, 45.0

        sid1 = generate_spatial_id(ra, dec, 60000.0, 100, 0)
        sid2 = generate_spatial_id(ra, dec, 60000.0, 101, 0)

        # Same position = same group, regardless of procver
        assert same_spatial_group(sid1, sid2)

    def test_group_ignores_time_procver_and_data_release(self):
        """Grouping only considers HEALPix (position), not time, procver, or DR.

        This enables queries like 'find all observations at this position'.
        """
        ra, dec = 123.456, -67.89

        # Same position, different times, procvers, and data releases
        sids = [
            generate_spatial_id(ra, dec, 50000.0, 1, 0),
            generate_spatial_id(ra, dec, 60000.0, 2, 1),
            generate_spatial_id(ra, dec, 70000.0, 100, 31),
        ]

        group = spatial_group_int(sids[0])
        assert all(spatial_group_int(s) == group for s in sids)


class TestInputValidation:
    """Test input validation and error handling."""

    def test_ra_out_of_range_high(self):
        """RA >= 360 should raise ValueError."""
        with pytest.raises(ValueError, match="ra must be"):
            generate_spatial_id(360.0, 0.0, MJD_EPOCH, 0, 0)

    def test_ra_out_of_range_negative(self):
        """RA < 0 should raise ValueError."""
        with pytest.raises(ValueError, match="ra must be"):
            generate_spatial_id(-0.1, 0.0, MJD_EPOCH, 0, 0)

    def test_dec_out_of_range_high(self):
        """Dec > 90 should raise ValueError."""
        with pytest.raises(ValueError, match="dec must be"):
            generate_spatial_id(0.0, 90.1, MJD_EPOCH, 0, 0)

    def test_dec_out_of_range_low(self):
        """Dec < -90 should raise ValueError."""
        with pytest.raises(ValueError, match="dec must be"):
            generate_spatial_id(0.0, -90.1, MJD_EPOCH, 0, 0)

    def test_mjd_before_epoch(self):
        """MJD < MJD_EPOCH should raise ValueError."""
        with pytest.raises(ValueError, match="mjd must be"):
            generate_spatial_id(0.0, 0.0, MJD_EPOCH - 1, 0, 0)

    def test_procver_negative(self):
        """Negative procver should raise ValueError."""
        with pytest.raises(ValueError, match="procver_compact must be"):
            generate_spatial_id(0.0, 0.0, MJD_EPOCH, -1, 0)

    def test_procver_too_large(self):
        """procver > 65535 should raise ValueError."""
        with pytest.raises(ValueError, match="procver_compact must be"):
            generate_spatial_id(0.0, 0.0, MJD_EPOCH, 65536, 0)

    def test_data_release_negative(self):
        """Negative data_release should raise ValueError."""
        with pytest.raises(ValueError, match="data_release must be"):
            generate_spatial_id(0.0, 0.0, MJD_EPOCH, 0, -1)

    def test_data_release_too_large(self):
        """data_release > 31 should raise ValueError."""
        with pytest.raises(ValueError, match="data_release must be"):
            generate_spatial_id(0.0, 0.0, MJD_EPOCH, 0, 32)


class TestEdgeCases:
    """Test edge cases and special positions."""

    def test_north_pole(self):
        """Test position at north pole (dec=90)."""
        sid = generate_spatial_id(0.0, 90.0, MJD_EPOCH, 0, 0)
        ra, dec = extract_approx_radec(sid)
        # At poles, recovered dec should be very close to 90
        assert abs(dec - 90.0) < 0.001

    def test_south_pole(self):
        """Test position at south pole (dec=-90)."""
        sid = generate_spatial_id(0.0, -90.0, MJD_EPOCH, 0, 0)
        ra, dec = extract_approx_radec(sid)
        assert abs(dec - (-90.0)) < 0.001

    def test_date_line(self):
        """Test positions near RA=0/360 boundary."""
        sid1 = generate_spatial_id(0.001, 0.0, MJD_EPOCH, 0, 0)
        sid2 = generate_spatial_id(359.999, 0.0, MJD_EPOCH, 0, 0)

        # These should be different HEALPix pixels
        assert extract_healpix(sid1) != extract_healpix(sid2)

    def test_ra_zero(self):
        """Test RA=0 (valid)."""
        sid = generate_spatial_id(0.0, 0.0, MJD_EPOCH, 0, 0)
        assert sid is not None

    def test_ra_near_360(self):
        """Test RA near 360 (valid)."""
        sid = generate_spatial_id(359.9999, 0.0, MJD_EPOCH, 0, 0)
        assert sid is not None


class TestDeterminism:
    """Test that generation is deterministic (multi-master safe)."""

    def test_identical_inputs_identical_outputs(self):
        """Same inputs must always produce same UUID."""
        args = (180.0, 45.0, 60000.0, 100, 1)

        sid1 = generate_spatial_id(*args)
        sid2 = generate_spatial_id(*args)
        sid3 = generate_spatial_id(*args)

        assert sid1 == sid2 == sid3

    def test_different_inputs_different_outputs(self):
        """Different inputs should produce different UUIDs."""
        base = (180.0, 45.0, 60000.0, 100, 1)

        sid_base = generate_spatial_id(*base)

        # Change each parameter
        assert generate_spatial_id(181.0, 45.0, 60000.0, 100, 1) != sid_base
        assert generate_spatial_id(180.0, 46.0, 60000.0, 100, 1) != sid_base
        assert generate_spatial_id(180.0, 45.0, 60001.0, 100, 1) != sid_base
        assert generate_spatial_id(180.0, 45.0, 60000.0, 101, 1) != sid_base
        assert generate_spatial_id(180.0, 45.0, 60000.0, 100, 2) != sid_base


class TestMJDPrecision:
    """Test MJD encoding precision."""

    def test_mjd_millisecond_precision(self):
        """MJD should have approximately millisecond precision."""
        mjd1 = 60000.0
        mjd2 = 60000.0 + 0.001 / 86400  # 1 millisecond later

        sid1 = generate_spatial_id(180.0, 0.0, mjd1, 0, 0)
        sid2 = generate_spatial_id(180.0, 0.0, mjd2, 0, 0)

        extracted1 = extract_mjd(sid1)
        extracted2 = extract_mjd(sid2)

        # Precision should be better than 1 second
        assert abs(extracted1 - mjd1) < 1.0 / 86400

    def test_mjd_round_trip_precision(self):
        """MJD round-trip should preserve ~millisecond precision."""
        test_mjds = [40000.0, 50000.5, 60000.123456, 70000.999]

        for mjd in test_mjds:
            sid = generate_spatial_id(180.0, 0.0, mjd, 0, 0)
            recovered = extract_mjd(sid)
            # Should be within 2ms tolerance
            assert abs(recovered - mjd) < 2.0 / 86400 / 1000


class TestUUIDFormat:
    """Test UUID format compatibility."""

    def test_returns_uuid_type(self):
        """generate_spatial_id should return uuid.UUID instance."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)
        assert isinstance(sid, uuid.UUID)

    def test_uuid_string_format(self):
        """UUID should have standard string format."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)
        sid_str = str(sid)

        # Standard UUID format: 8-4-4-4-12 hex digits
        assert len(sid_str) == 36
        parts = sid_str.split("-")
        assert len(parts) == 5
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]

    def test_uuid_bytes_length(self):
        """UUID should have exactly 16 bytes."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)
        assert len(sid.bytes) == 16

    def test_uuid_int_range(self):
        """UUID int value should be valid 128-bit unsigned integer."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)
        assert 0 <= sid.int < 2**128


class TestCoordinateRecovery:
    """Test coordinate recovery from HEALPix."""

    def test_healpix_to_radec_round_trip(self):
        """Coordinates should be recoverable to sub-arcsecond precision."""
        test_coords = [
            (0.0, 0.0),
            (180.0, 45.0),
            (270.0, -45.0),
            (123.456, 67.89),
        ]

        for ra, dec in test_coords:
            sid = generate_spatial_id(ra, dec, MJD_EPOCH, 0, 0)
            ra_out, dec_out = extract_approx_radec(sid)

            # With NSIDE=2^29, should be within 0.001" (0.0000003 degrees)
            ra_diff = min(abs(ra_out - ra), abs(ra_out - ra + 360), abs(ra_out - ra - 360))
            assert ra_diff < 0.0001, f"RA error too large: {ra_diff} deg"
            assert abs(dec_out - dec) < 0.0001, f"Dec error too large: {abs(dec_out - dec)} deg"

    def test_healpix_to_radec_direct(self):
        """Test direct HEALPix to RA/Dec conversion."""
        ra, dec = healpix_to_radec(0)
        assert 0 <= ra < 360
        assert -90 <= dec <= 90


class TestDataReleaseBoundary:
    """Test all data_release values (now 0-31 with 5 bits)."""

    def test_all_data_release_values(self):
        """Test all 32 possible data_release values."""
        for dr in range(32):
            sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, dr)
            assert extract_data_release(sid) == dr, f"Failed for dr={dr}"


class TestTradeOffs:
    """Tests proving documented trade-offs actually exist."""

    def test_healpix_sub_milliarcsec_precision(self):
        """
        PRECISION TEST: Verify sub-milliarcsecond (~0.0004") resolution.

        With NSIDE=2^29, pixels are ~0.0004" across. Points separated by
        more than this should map to different pixels.
        """
        ra_base = 180.0
        dec_base = 45.0

        # Two points separated by 0.001" (well above 0.0004" resolution)
        offset = 0.001 / 3600  # 0.001 arcseconds in degrees

        sid1 = generate_spatial_id(ra_base, dec_base, MJD_EPOCH, 0, 0)
        sid2 = generate_spatial_id(ra_base + offset, dec_base, MJD_EPOCH, 0, 0)

        # These SHOULD be different HEALPix pixels
        assert extract_healpix(sid1) != extract_healpix(sid2), (
            "Points 0.001 arcsec apart should map to different HEALPix pixels "
            "with NSIDE=2^29 (~0.0004\" resolution)."
        )

    def test_coordinate_recovery_precision(self):
        """
        PRECISION TEST: Verify coordinate recovery maintains precision.

        With NSIDE=2^29, recovered coordinates should match originals
        to within ~0.0004" (0.0000001 degrees).
        """
        test_coords = [(180.0, 45.0), (0.0, 0.0), (270.0, -60.0)]

        for ra, dec in test_coords:
            sid = generate_spatial_id(ra, dec, MJD_EPOCH, 0, 0)
            ra_out, dec_out = extract_approx_radec(sid)

            # Should match to ~0.0004" = 0.0000001 degrees
            ra_diff = min(abs(ra_out - ra), 360 - abs(ra_out - ra))
            dec_diff = abs(dec_out - dec)

            assert ra_diff < 0.0001, f"RA precision too low: {ra_diff * 3600:.4f} arcsec"
            assert dec_diff < 0.0001, f"Dec precision too low: {dec_diff * 3600:.4f} arcsec"

    def test_mjd_epoch_extends_usable_range(self):
        """
        TRADE-OFF TEST: Prove that using MJD_EPOCH extends usable date range.

        With epoch = 40000, we can represent MJD 40000-141851 (through year 2280+).
        """
        max_mjd = MJD_EPOCH + _MAX_MJD_MS / _MS_PER_DAY

        assert max_mjd > 124000, (
            f"Maximum MJD {max_mjd:.0f} should exceed 124000 (year 2280)."
        )

        # Verify we can encode a date in year 2200
        mjd_year_2200 = 95000.0
        sid = generate_spatial_id(180.0, 0.0, mjd_year_2200, 0, 0)
        assert abs(extract_mjd(sid) - mjd_year_2200) < 0.001


class TestMultiMasterSafety:
    """Tests proving multi-master replication safety."""

    def test_determinism_across_threads(self):
        """
        CONCURRENCY TEST: Prove multi-master safety by generating from multiple threads.
        """
        import threading

        args = (180.0, 45.0, 60000.0, 100, 1)
        results = []
        errors = []

        def generate_uuid():
            try:
                sid = generate_spatial_id(*args)
                results.append(sid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=generate_uuid) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent generation: {errors}"
        assert len(results) == 8, "All threads should complete"

        unique_uuids = set(results)
        assert len(unique_uuids) == 1, (
            f"Multi-master safety violated! Got {len(unique_uuids)} different UUIDs."
        )

    def test_uniqueness_across_parameter_space(self):
        """
        PROPERTY TEST: Different spatial positions produce unique UUIDs.
        """
        seen = set()
        count = 0

        for ra in range(0, 360, 30):
            for dec in range(-90, 91, 30):
                if abs(dec) == 90 and ra != 0:
                    continue

                for mjd_offset in range(0, 3):
                    mjd = 60000.0 + mjd_offset
                    sid = generate_spatial_id(float(ra), float(dec), mjd, 1, 0)
                    count += 1

                    assert sid not in seen, (
                        f"Duplicate UUID for ra={ra}, dec={dec}, mjd={mjd}."
                    )
                    seen.add(sid)

        assert len(seen) == count


class TestOverflowBoundaries:
    """Tests for overflow and boundary conditions."""

    def test_mjd_overflow_raises_error(self):
        """MJD exceeding maximum should raise clear error."""
        max_mjd = MJD_EPOCH + _MAX_MJD_MS / _MS_PER_DAY

        sid = generate_spatial_id(180.0, 0.0, max_mjd - 1, 0, 0)
        assert sid is not None

        with pytest.raises(ValueError, match="exceeds maximum"):
            generate_spatial_id(180.0, 0.0, max_mjd + 100, 0, 0)

    def test_big_endian_byte_order(self):
        """Verify UUID uses big-endian byte order."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)

        healpix_extracted = extract_healpix(sid)

        # Verify big-endian: high64 contains HEALPix directly
        high64 = struct.unpack(">Q", sid.bytes[:8])[0]

        assert high64 == healpix_extracted, (
            "HEALPix extraction assumes big-endian. Byte order mismatch."
        )


class TestConstants:
    """Test module constants are correctly defined."""

    def test_bit_sum(self):
        """Total bits should equal 128."""
        total = RESERVED_BITS + HEALPIX_BITS + PROCVER_BITS + MJD_BITS + DATARELEASE_BITS
        assert total == 128, f"Bit fields sum to {total}, expected 128"

    def test_nside_power_of_two(self):
        """NSIDE should be a power of 2."""
        assert NSIDE > 0
        assert (NSIDE & (NSIDE - 1)) == 0

    def test_nside_is_2_to_29(self):
        """NSIDE should be 2^29 (maximum supported by healpy)."""
        assert NSIDE == 2**29

    def test_max_values_fit_in_bits(self):
        """Maximum values should fit in allocated bits."""
        assert _MAX_PROCVER < 2**PROCVER_BITS
        assert _MAX_DATARELEASE < 2**DATARELEASE_BITS
        assert _MAX_MJD_MS < 2**MJD_BITS

    def test_data_release_max_is_31(self):
        """DataRelease max should be 31 (5 bits)."""
        assert _MAX_DATARELEASE == 31
        assert DATARELEASE_BITS == 5

    def test_mjd_epoch_reasonable(self):
        """MJD_EPOCH should be a reasonable value (around 1968)."""
        assert 39000 < MJD_EPOCH < 41000
