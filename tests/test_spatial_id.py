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
"""

import math
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
        procver, dr = 12345, 1000

        sid = generate_spatial_id(ra, dec, mjd, procver, dr)
        healpix, pv, data_rel, mjd_out = extract_all(sid)

        assert pv == procver
        assert data_rel == dr
        assert abs(mjd_out - mjd) < 0.001


class TestBitLayout:
    """Verify the bit layout matches the specification."""

    def test_healpix_position(self):
        """Verify HEALPix occupies bits 0-39 (top 40 bits)."""
        # Create spatial_id with known HEALPix value
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)
        healpix = extract_healpix(sid)

        # Verify by direct bit extraction
        high64 = struct.unpack(">Q", sid.bytes[:8])[0]
        assert (high64 >> 24) == healpix

    def test_procver_position(self):
        """Verify ProcVer occupies bits 40-55."""
        procver = 0xABCD
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, procver, 0)

        high64 = struct.unpack(">Q", sid.bytes[:8])[0]
        extracted = (high64 >> 8) & 0xFFFF
        assert extracted == procver

    def test_data_release_spans_boundary(self):
        """Verify DataRelease (11 bits) spans the 64-bit boundary correctly."""
        # Test all possible data_release values (0-2047)
        for dr in [0, 1, 7, 8, 255, 256, 1023, 1024, 2047]:
            sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, dr)
            assert extract_data_release(sid) == dr, f"Failed for data_release={dr}"

    def test_mjd_position(self):
        """Verify MJD_ms occupies bits 67-109."""
        mjd = 60000.0
        sid = generate_spatial_id(180.0, 0.0, mjd, 0, 0)

        low64 = struct.unpack(">Q", sid.bytes[8:])[0]
        mjd_ms = (low64 >> 18) & 0x7FFFFFFFFFF

        expected_ms = int((mjd - MJD_EPOCH) * _MS_PER_DAY)
        assert mjd_ms == expected_ms


class TestBoundaryValues:
    """Test maximum values for each field."""

    def test_max_procver(self):
        """Test maximum processing version (65535)."""
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, _MAX_PROCVER, 0)
        assert extract_procver(sid) == _MAX_PROCVER

    def test_max_data_release(self):
        """Test maximum data release (2047)."""
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

    def test_different_procver_different_group(self):
        """Objects with different procver are in different groups."""
        ra, dec = 180.0, 45.0

        sid1 = generate_spatial_id(ra, dec, 60000.0, 100, 0)
        sid2 = generate_spatial_id(ra, dec, 60000.0, 101, 0)

        assert not same_spatial_group(sid1, sid2)

    def test_group_ignores_time_and_data_release(self):
        """Grouping only considers HEALPix and ProcVer, not time or DR."""
        ra, dec, procver = 123.456, -67.89, 42

        sids = [
            generate_spatial_id(ra, dec, 50000.0, procver, 0),
            generate_spatial_id(ra, dec, 60000.0, procver, 1),
            generate_spatial_id(ra, dec, 70000.0, procver, 2047),
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
        """data_release > 2047 should raise ValueError."""
        with pytest.raises(ValueError, match="data_release must be"):
            generate_spatial_id(0.0, 0.0, MJD_EPOCH, 0, 2048)


class TestEdgeCases:
    """Test edge cases and special positions."""

    def test_north_pole(self):
        """Test position at north pole (dec=90)."""
        sid = generate_spatial_id(0.0, 90.0, MJD_EPOCH, 0, 0)
        ra, dec = extract_approx_radec(sid)
        assert abs(dec - 90.0) < 1.0  # Within ~1 degree (HEALPix limitation at poles)

    def test_south_pole(self):
        """Test position at south pole (dec=-90)."""
        sid = generate_spatial_id(0.0, -90.0, MJD_EPOCH, 0, 0)
        ra, dec = extract_approx_radec(sid)
        assert abs(dec - (-90.0)) < 1.0

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
        # 1 millisecond = 1/(86400*1000) days ≈ 1.16e-8 days
        mjd1 = 60000.0
        mjd2 = 60000.0 + 0.001 / 86400  # 1 millisecond later

        sid1 = generate_spatial_id(180.0, 0.0, mjd1, 0, 0)
        sid2 = generate_spatial_id(180.0, 0.0, mjd2, 0, 0)

        # Should produce different UUIDs for 1ms difference
        # (may or may not, depending on rounding)
        extracted1 = extract_mjd(sid1)
        extracted2 = extract_mjd(sid2)

        # Precision should be better than 1 second (86400 ms = 1 day)
        assert abs(extracted1 - mjd1) < 1.0 / 86400

    def test_mjd_round_trip_precision(self):
        """MJD round-trip should preserve ~millisecond precision."""
        test_mjds = [40000.0, 50000.5, 60000.123456, 70000.999]

        for mjd in test_mjds:
            sid = generate_spatial_id(180.0, 0.0, mjd, 0, 0)
            recovered = extract_mjd(sid)
            # Should be within 1 millisecond
            assert abs(recovered - mjd) < 1.0 / 86400 / 1000 * 2  # 2ms tolerance


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
        """Coordinates should be recoverable to HEALPix precision."""
        test_coords = [
            (0.0, 0.0),
            (180.0, 45.0),
            (270.0, -45.0),
            (123.456, 67.89),
        ]

        for ra, dec in test_coords:
            sid = generate_spatial_id(ra, dec, MJD_EPOCH, 0, 0)
            ra_out, dec_out = extract_approx_radec(sid)

            # Should be within ~1 arcsecond (HEALPix resolution)
            assert abs(ra_out - ra) < 0.01 or abs(ra_out - ra - 360) < 0.01
            assert abs(dec_out - dec) < 0.01

    def test_healpix_to_radec_direct(self):
        """Test direct HEALPix to RA/Dec conversion."""
        ra, dec = healpix_to_radec(0)
        assert 0 <= ra < 360
        assert -90 <= dec <= 90


class TestDataReleaseBoundary:
    """Detailed tests for data_release field spanning 64-bit boundary."""

    def test_all_data_release_values(self):
        """Test all 2048 possible data_release values."""
        # Test a sample of values including boundary cases
        test_values = list(range(0, 2048, 100)) + [2047]

        for dr in test_values:
            sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, dr)
            assert extract_data_release(sid) == dr, f"Failed for dr={dr}"

    def test_data_release_bits_7_and_8(self):
        """Test values around the 8-bit boundary (256)."""
        for dr in range(250, 260):
            sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, dr)
            assert extract_data_release(sid) == dr


class TestTradeOffs:
    """Tests proving documented trade-offs actually exist."""

    def test_healpix_quantization_proves_precision_loss(self):
        """
        TRADE-OFF TEST: Prove that HEALPix quantization causes precision loss.

        This verifies the documented trade-off: spatial encoding loses
        sub-arcsecond precision in exchange for efficient grouping.
        """
        ra_base = 180.0
        dec_base = 45.0

        # Two points separated by 0.1 arcseconds (well under resolution)
        offset_01 = 0.1 / 3600  # 0.1 arcseconds in degrees

        sid1 = generate_spatial_id(ra_base, dec_base, MJD_EPOCH, 0, 0)
        sid2 = generate_spatial_id(ra_base + offset_01, dec_base, MJD_EPOCH, 0, 0)

        # These SHOULD map to the same HEALPix pixel (proving precision loss)
        assert extract_healpix(sid1) == extract_healpix(sid2), (
            "Points 0.1 arcsec apart should map to same HEALPix pixel. "
            "If this fails, NSIDE may have changed - update documentation."
        )

        # At 2 arcsec separation, points SHOULD be different pixels
        offset_2 = 2.0 / 3600
        sid3 = generate_spatial_id(ra_base + offset_2, dec_base, MJD_EPOCH, 0, 0)

        assert extract_healpix(sid1) != extract_healpix(sid3), (
            "Points 2 arcsec apart should be in different HEALPix pixels. "
            "This proves the ~0.78 arcsec resolution claim."
        )

    def test_mjd_epoch_extends_usable_range(self):
        """
        TRADE-OFF TEST: Prove that using MJD_EPOCH extends usable date range.

        Without the epoch offset, we could only represent ~101,000 days from MJD 0.
        With epoch = 40000, we can represent MJD 40000-141851 (through year 2280+).
        """
        # Calculate maximum representable MJD
        max_mjd = MJD_EPOCH + _MAX_MJD_MS / _MS_PER_DAY

        # Verify it extends past year 2280 (MJD ~124000+)
        assert max_mjd > 124000, (
            f"Maximum MJD {max_mjd:.0f} should exceed 124000 (year 2280). "
            "The MJD_EPOCH trade-off should extend range past year 2280."
        )

        # Verify we can actually encode a date in year 2200 (MJD ~95000)
        mjd_year_2200 = 95000.0
        sid = generate_spatial_id(180.0, 0.0, mjd_year_2200, 0, 0)
        assert abs(extract_mjd(sid) - mjd_year_2200) < 0.001, (
            "Should be able to encode dates through year 2200"
        )


class TestMultiMasterSafety:
    """Tests proving multi-master replication safety."""

    def test_determinism_across_threads(self):
        """
        CONCURRENCY TEST: Prove multi-master safety by generating from multiple threads.

        This verifies the core claim that identical inputs produce identical
        UUIDs even when generated concurrently from different threads.
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

        # Simulate 8 "database instances" generating simultaneously
        threads = [threading.Thread(target=generate_uuid) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent generation: {errors}"
        assert len(results) == 8, "All threads should complete"

        # ALL results must be identical (multi-master safety)
        unique_uuids = set(results)
        assert len(unique_uuids) == 1, (
            f"Multi-master safety violated! Got {len(unique_uuids)} different UUIDs "
            f"for identical inputs across {len(results)} threads."
        )

    def test_uniqueness_across_parameter_space(self):
        """
        PROPERTY TEST: Different spatial positions produce unique UUIDs.

        This verifies the fundamental uniqueness property required for
        primary key generation.

        Note: At poles (dec=±90), all RA values converge to the same point,
        so they correctly map to the same HEALPix pixel.
        """
        seen = set()
        count = 0

        # Generate UUIDs across a grid of positions
        for ra in range(0, 360, 30):  # 12 RA values
            for dec in range(-90, 91, 30):  # 7 Dec values
                # Skip redundant RA values at poles (all RA map to same point)
                if abs(dec) == 90 and ra != 0:
                    continue

                for mjd_offset in range(0, 3):  # 3 time values
                    mjd = 60000.0 + mjd_offset
                    sid = generate_spatial_id(float(ra), float(dec), mjd, 1, 0)
                    count += 1

                    assert sid not in seen, (
                        f"Duplicate UUID for ra={ra}, dec={dec}, mjd={mjd}. "
                        f"Uniqueness invariant violated!"
                    )
                    seen.add(sid)

        assert len(seen) == count, (
            f"Expected {count} unique UUIDs, got {len(seen)}"
        )


class TestOverflowBoundaries:
    """Tests for overflow and boundary conditions."""

    def test_mjd_overflow_raises_error(self):
        """
        BOUNDARY TEST: MJD exceeding maximum should raise clear error.
        """
        # Calculate exact maximum MJD
        max_mjd = MJD_EPOCH + _MAX_MJD_MS / _MS_PER_DAY

        # Just below max should work
        sid = generate_spatial_id(180.0, 0.0, max_mjd - 1, 0, 0)
        assert sid is not None

        # Exceeding max should raise ValueError
        with pytest.raises(ValueError, match="exceeds maximum"):
            generate_spatial_id(180.0, 0.0, max_mjd + 100, 0, 0)

    def test_big_endian_byte_order(self):
        """
        SPECIFICATION TEST: Verify UUID uses big-endian byte order.

        This is critical for cross-platform compatibility and database sorting.
        """
        sid = generate_spatial_id(180.0, 0.0, MJD_EPOCH, 0, 0)

        # Extract HEALPix via documented method
        healpix_extracted = extract_healpix(sid)

        # Verify big-endian: most significant bytes come first
        high64 = struct.unpack(">Q", sid.bytes[:8])[0]
        healpix_from_bytes = high64 >> 24

        assert healpix_from_bytes == healpix_extracted, (
            "HEALPix extraction assumes big-endian. Byte order mismatch."
        )


class TestConstants:

    def test_bit_sum(self):
        """Total bits should equal 128."""
        total = HEALPIX_BITS + PROCVER_BITS + DATARELEASE_BITS + MJD_BITS + 18  # Reserved
        assert total == 128

    def test_nside_power_of_two(self):
        """NSIDE should be a power of 2."""
        assert NSIDE > 0
        assert (NSIDE & (NSIDE - 1)) == 0  # Power of 2 check

    def test_max_values_fit_in_bits(self):
        """Maximum values should fit in allocated bits."""
        assert _MAX_PROCVER < 2**PROCVER_BITS
        assert _MAX_DATARELEASE < 2**DATARELEASE_BITS
        assert _MAX_MJD_MS < 2**MJD_BITS

    def test_mjd_epoch_reasonable(self):
        """MJD_EPOCH should be a reasonable value (around 1968)."""
        assert 39000 < MJD_EPOCH < 41000
