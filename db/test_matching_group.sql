-- Test suite for matching_group PostgreSQL functions
-- Run with: psql -d fastdb -f test_matching_group.sql
--
-- These tests verify:
-- 1. Basic functionality of all matching_group functions
-- 2. Edge cases and boundary conditions
-- 3. Consistency with expected values
-- 4. Error handling

-- =============================================================================
-- Test Framework
-- =============================================================================
CREATE OR REPLACE FUNCTION _test_assert(condition BOOLEAN, message TEXT) RETURNS VOID AS $$
BEGIN
    IF NOT condition THEN
        RAISE EXCEPTION 'ASSERTION FAILED: %', message;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION _test_assert_equals(actual BIGINT, expected BIGINT, message TEXT) RETURNS VOID AS $$
BEGIN
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'ASSERTION FAILED: % - Expected %, got %', message, expected, actual;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION _test_assert_array_contains(arr BIGINT[], value BIGINT, message TEXT) RETURNS VOID AS $$
BEGIN
    IF NOT (value = ANY(arr)) THEN
        RAISE EXCEPTION 'ASSERTION FAILED: % - Array does not contain %', message, value;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Test: _mg_undilate (Morton code bit extraction)
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=== Testing _mg_undilate ===';

    -- Test 1: Zero input
    PERFORM _test_assert_equals(_mg_undilate(0), 0, 'undilate(0) should be 0');

    -- Test 2: Alternating bits pattern (0101... = x'5555...')
    -- undilate extracts every other bit, so 0b0101 -> 0b11 = 3
    PERFORM _test_assert_equals(_mg_undilate(5), 3, 'undilate(5) should extract bits to 3');

    -- Test 3: Another pattern - 0b1010 = 10 -> undilate should give 0b00 = 0
    PERFORM _test_assert_equals(_mg_undilate(10), 0, 'undilate(10) should be 0');

    -- Test 4: 0b10001 = 17 -> undilate extracts bits 0,2,4 -> 0b101 = 5
    PERFORM _test_assert_equals(_mg_undilate(17), 5, 'undilate(17) should be 5');

    RAISE NOTICE 'PASSED: _mg_undilate tests';
END $$;

-- =============================================================================
-- Test: _mg_dilate (Morton code bit spreading)
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=== Testing _mg_dilate ===';

    -- Test 1: Zero input
    PERFORM _test_assert_equals(_mg_dilate(0), 0, 'dilate(0) should be 0');

    -- Test 2: Simple value - 0b11 = 3 -> dilate spreads to 0b0101 = 5
    PERFORM _test_assert_equals(_mg_dilate(3), 5, 'dilate(3) should be 5');

    -- Test 3: 0b101 = 5 -> dilate spreads to 0b010001 = 17
    PERFORM _test_assert_equals(_mg_dilate(5), 17, 'dilate(5) should be 17');

    -- Test 4: Round-trip - dilate then undilate should return original
    PERFORM _test_assert_equals(_mg_undilate(_mg_dilate(12345)), 12345,
        'undilate(dilate(x)) should equal x');

    RAISE NOTICE 'PASSED: _mg_dilate tests';
END $$;

-- =============================================================================
-- Test: _mg_xy_to_morton
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=== Testing _mg_xy_to_morton ===';

    -- Test 1: Origin
    PERFORM _test_assert_equals(_mg_xy_to_morton(0, 0), 0, 'morton(0,0) should be 0');

    -- Test 2: x=1, y=0 -> Morton = 0b01 = 1
    PERFORM _test_assert_equals(_mg_xy_to_morton(1, 0), 1, 'morton(1,0) should be 1');

    -- Test 3: x=0, y=1 -> Morton = 0b10 = 2
    PERFORM _test_assert_equals(_mg_xy_to_morton(0, 1), 2, 'morton(0,1) should be 2');

    -- Test 4: x=1, y=1 -> Morton = 0b11 = 3
    PERFORM _test_assert_equals(_mg_xy_to_morton(1, 1), 3, 'morton(1,1) should be 3');

    -- Test 5: x=2, y=0 -> Morton = 0b0100 = 4
    PERFORM _test_assert_equals(_mg_xy_to_morton(2, 0), 4, 'morton(2,0) should be 4');

    RAISE NOTICE 'PASSED: _mg_xy_to_morton tests';
END $$;

-- =============================================================================
-- Test: matching_group (main function)
-- =============================================================================
DO $$
DECLARE
    test_uuid UUID;
    mg1 BIGINT;
    mg2 BIGINT;
BEGIN
    RAISE NOTICE '=== Testing matching_group ===';

    -- Test 1: Basic extraction - non-null result
    test_uuid := '06780000-6660-7fe0-324a-9a7000000000'::UUID;
    mg1 := matching_group(test_uuid);
    PERFORM _test_assert(mg1 IS NOT NULL, 'matching_group should not return NULL');

    -- Test 2: Determinism - same input = same output
    mg2 := matching_group(test_uuid);
    PERFORM _test_assert_equals(mg1, mg2, 'matching_group should be deterministic');

    -- Test 3: Different UUIDs produce different results
    mg2 := matching_group('11111111-1111-1111-1111-111111111111'::UUID);
    PERFORM _test_assert(mg1 != mg2, 'Different UUIDs should produce different matching_groups');

    -- Test 4: Known value verification (from Python: spatial_id at ra=120, dec=45)
    -- Python: matching_group_int() returns 434110465 for this UUID
    test_uuid := '06780000-6660-7fe0-324a-9a7000000000'::UUID;
    mg1 := matching_group(test_uuid);
    PERFORM _test_assert_equals(mg1, 434110465,
        'matching_group should match Python value 434110465');

    -- Test 5: Zero UUID
    test_uuid := '00000000-0000-0000-0000-000000000000'::UUID;
    mg1 := matching_group(test_uuid);
    PERFORM _test_assert_equals(mg1, 0, 'matching_group of zero UUID should be 0');

    RAISE NOTICE 'PASSED: matching_group tests';
END $$;

-- =============================================================================
-- Test: matching_group_neighbors
-- =============================================================================
DO $$
DECLARE
    mg BIGINT := 434110465;  -- Known matching_group value
    neighbors BIGINT[];
    neighbor_count INT;
BEGIN
    RAISE NOTICE '=== Testing matching_group_neighbors ===';

    -- Test 1: Should include self
    neighbors := matching_group_neighbors(mg);
    PERFORM _test_assert_array_contains(neighbors, mg, 'neighbors should include self');

    -- Test 2: Should have 1-9 elements
    neighbor_count := array_length(neighbors, 1);
    PERFORM _test_assert(neighbor_count >= 1 AND neighbor_count <= 9,
        'neighbors should have 1-9 elements, got ' || neighbor_count);

    -- Test 3: All values should be non-negative
    PERFORM _test_assert(
        (SELECT bool_and(n >= 0) FROM unnest(neighbors) AS n),
        'all neighbors should be non-negative');

    -- Test 4: All values should be unique
    PERFORM _test_assert(
        neighbor_count = (SELECT COUNT(DISTINCT n) FROM unnest(neighbors) AS n),
        'all neighbors should be unique');

    -- Test 5: Self should be first element
    PERFORM _test_assert_equals(neighbors[1], mg, 'self should be first element');

    -- Test 6: Interior pixel should have 9 neighbors
    -- Use a pixel known to be in interior of a base pixel
    mg := 1000000;  -- Arbitrary interior pixel
    neighbors := matching_group_neighbors(mg);
    neighbor_count := array_length(neighbors, 1);
    PERFORM _test_assert_equals(neighbor_count::BIGINT, 9,
        'interior pixel should have 9 neighbors');

    RAISE NOTICE 'PASSED: matching_group_neighbors tests';
END $$;

-- =============================================================================
-- Test: _mg_nside_for_precision
-- =============================================================================
DO $$
DECLARE
    nside BIGINT;
BEGIN
    RAISE NOTICE '=== Testing _mg_nside_for_precision ===';

    -- Test 1: 1 arcsec precision -> NSIDE = 2^17 = 131072
    nside := _mg_nside_for_precision(1.0);
    PERFORM _test_assert_equals(nside, 131072, '1" precision should give NSIDE=131072');

    -- Test 2: 10 arcsec precision -> NSIDE = 2^14 = 16384
    nside := _mg_nside_for_precision(10.0);
    PERFORM _test_assert_equals(nside, 16384, '10" precision should give NSIDE=16384');

    -- Test 3: 100 arcsec precision -> NSIDE = 2^11 = 2048
    nside := _mg_nside_for_precision(100.0);
    PERFORM _test_assert_equals(nside, 2048, '100" precision should give NSIDE=2048');

    -- Test 4: Very fine precision (0.001") -> NSIDE = 2^27 = 134217728
    -- 206265 / 0.001 = 206265000, largest power of 2 <= that is 2^27
    nside := _mg_nside_for_precision(0.001);
    PERFORM _test_assert_equals(nside, 134217728, '0.001" precision should give NSIDE=2^27');

    -- Test 5: Very coarse precision (1000") -> NSIDE = 2^7 = 128
    nside := _mg_nside_for_precision(1000.0);
    PERFORM _test_assert_equals(nside, 128, '1000" precision should give NSIDE=128');

    RAISE NOTICE 'PASSED: _mg_nside_for_precision tests';
END $$;

-- =============================================================================
-- Test: _mg_nside_for_precision error handling
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=== Testing _mg_nside_for_precision error handling ===';

    -- Test 1: Zero precision should raise error
    BEGIN
        PERFORM _mg_nside_for_precision(0.0);
        RAISE EXCEPTION 'Should have raised error for precision=0';
    EXCEPTION WHEN OTHERS THEN
        PERFORM _test_assert(SQLERRM LIKE '%Precision must be > 0%',
            'Should raise error for precision=0');
    END;

    -- Test 2: Negative precision should raise error
    BEGIN
        PERFORM _mg_nside_for_precision(-1.0);
        RAISE EXCEPTION 'Should have raised error for negative precision';
    EXCEPTION WHEN OTHERS THEN
        PERFORM _test_assert(SQLERRM LIKE '%Precision must be > 0%',
            'Should raise error for negative precision');
    END;

    RAISE NOTICE 'PASSED: _mg_nside_for_precision error handling tests';
END $$;

-- =============================================================================
-- Test: _mg_shift_bits_for_nside
-- =============================================================================
DO $$
DECLARE
    shift INT;
BEGIN
    RAISE NOTICE '=== Testing _mg_shift_bits_for_nside ===';

    -- Test 1: NSIDE=2^14 -> shift = 2*(29-14) = 30
    shift := _mg_shift_bits_for_nside(16384);
    PERFORM _test_assert_equals(shift::BIGINT, 30, 'NSIDE=16384 should give shift=30');

    -- Test 2: NSIDE=2^17 -> shift = 2*(29-17) = 24
    shift := _mg_shift_bits_for_nside(131072);
    PERFORM _test_assert_equals(shift::BIGINT, 24, 'NSIDE=131072 should give shift=24');

    -- Test 3: NSIDE=2^11 -> shift = 2*(29-11) = 36
    shift := _mg_shift_bits_for_nside(2048);
    PERFORM _test_assert_equals(shift::BIGINT, 36, 'NSIDE=2048 should give shift=36');

    -- Test 4: NSIDE=2^29 -> shift = 0 (storage resolution)
    shift := _mg_shift_bits_for_nside(536870912);
    PERFORM _test_assert_equals(shift::BIGINT, 0, 'NSIDE=2^29 should give shift=0');

    RAISE NOTICE 'PASSED: _mg_shift_bits_for_nside tests';
END $$;

-- =============================================================================
-- Test: matching_group_at_precision
-- =============================================================================
DO $$
DECLARE
    test_uuid UUID := '06780000-6660-7fe0-324a-9a7000000000'::UUID;
    mg_default BIGINT;
    mg_10 BIGINT;
    mg_1 BIGINT;
    mg_100 BIGINT;
BEGIN
    RAISE NOTICE '=== Testing matching_group_at_precision ===';

    -- Test 1: Default precision (10") should match matching_group()
    mg_default := matching_group(test_uuid);
    mg_10 := matching_group_at_precision(test_uuid, 10.0);
    PERFORM _test_assert_equals(mg_10, mg_default,
        'matching_group_at_precision(10) should equal matching_group()');

    -- Test 2: Finer precision (1") should give larger value
    mg_1 := matching_group_at_precision(test_uuid, 1.0);
    PERFORM _test_assert(mg_1 > mg_10,
        '1" precision should give larger matching_group than 10"');

    -- Test 3: Coarser precision (100") should give smaller value
    mg_100 := matching_group_at_precision(test_uuid, 100.0);
    PERFORM _test_assert(mg_100 < mg_10,
        '100" precision should give smaller matching_group than 10"');

    -- Test 4: Hierarchy check - coarser contains finer
    -- mg_100 should be mg_10 >> (30-36) = mg_10 >> -6 which is wrong
    -- Actually: mg_10 >> 6 should equal mg_100 (shift by additional 6 bits)
    PERFORM _test_assert_equals(mg_10 >> 6, mg_100,
        'Coarser precision should be finer >> additional_bits');

    -- Test 5: Known values from Python
    PERFORM _test_assert_equals(mg_1, 27783069798,
        'matching_group_at_precision(1) should match Python value');
    PERFORM _test_assert_equals(mg_10, 434110465,
        'matching_group_at_precision(10) should match Python value');
    PERFORM _test_assert_equals(mg_100, 6782976,
        'matching_group_at_precision(100) should match Python value');

    RAISE NOTICE 'PASSED: matching_group_at_precision tests';
END $$;

-- =============================================================================
-- Test: matching_group_neighbors_at_precision
-- =============================================================================
DO $$
DECLARE
    mg BIGINT;
    neighbors_1 BIGINT[];
    neighbors_10 BIGINT[];
    neighbors_100 BIGINT[];
BEGIN
    RAISE NOTICE '=== Testing matching_group_neighbors_at_precision ===';

    -- Test 1: 10" precision should match matching_group_neighbors
    mg := 434110465;
    neighbors_10 := matching_group_neighbors_at_precision(mg, 10.0);
    PERFORM _test_assert(
        neighbors_10 = matching_group_neighbors(mg),
        '10" precision neighbors should match default');

    -- Test 2: All precisions should include self
    mg := 27783069798;  -- 1" precision value
    neighbors_1 := matching_group_neighbors_at_precision(mg, 1.0);
    PERFORM _test_assert_array_contains(neighbors_1, mg, '1" neighbors should include self');

    mg := 6782976;  -- 100" precision value
    neighbors_100 := matching_group_neighbors_at_precision(mg, 100.0);
    PERFORM _test_assert_array_contains(neighbors_100, mg, '100" neighbors should include self');

    -- Test 3: All should have valid counts (1-9)
    PERFORM _test_assert(
        array_length(neighbors_1, 1) BETWEEN 1 AND 9,
        '1" neighbors should have 1-9 elements');
    PERFORM _test_assert(
        array_length(neighbors_100, 1) BETWEEN 1 AND 9,
        '100" neighbors should have 1-9 elements');

    RAISE NOTICE 'PASSED: matching_group_neighbors_at_precision tests';
END $$;

-- =============================================================================
-- Test: matching_precision_info
-- =============================================================================
DO $$
DECLARE
    info RECORD;
BEGIN
    RAISE NOTICE '=== Testing matching_precision_info ===';

    -- Test 1: 1" precision info
    SELECT * INTO info FROM matching_precision_info(1.0);
    PERFORM _test_assert_equals(info.nside, 131072, '1" nside should be 131072');
    PERFORM _test_assert_equals(info.shift_bits::BIGINT, 24, '1" shift_bits should be 24');
    PERFORM _test_assert(info.pixel_size_arcsec < 2.0, '1" pixel_size should be < 2"');

    -- Test 2: 10" precision info
    SELECT * INTO info FROM matching_precision_info(10.0);
    PERFORM _test_assert_equals(info.nside, 16384, '10" nside should be 16384');
    PERFORM _test_assert_equals(info.shift_bits::BIGINT, 30, '10" shift_bits should be 30');
    PERFORM _test_assert(info.pixel_size_arcsec BETWEEN 10.0 AND 15.0,
        '10" pixel_size should be ~12.6"');

    -- Test 3: 100" precision info
    SELECT * INTO info FROM matching_precision_info(100.0);
    PERFORM _test_assert_equals(info.nside, 2048, '100" nside should be 2048');
    PERFORM _test_assert_equals(info.shift_bits::BIGINT, 36, '100" shift_bits should be 36');
    PERFORM _test_assert(info.pixel_size_arcsec BETWEEN 90.0 AND 110.0,
        '100" pixel_size should be ~100"');

    -- Test 4: Total pixels sanity check
    SELECT * INTO info FROM matching_precision_info(10.0);
    PERFORM _test_assert_equals(info.total_pixels, 12 * 16384::BIGINT * 16384::BIGINT,
        'total_pixels should be 12 * NSIDE^2');

    RAISE NOTICE 'PASSED: matching_precision_info tests';
END $$;

-- =============================================================================
-- Test: Boundary pair detection (integration test)
-- =============================================================================
DO $$
DECLARE
    pair_count INT;
BEGIN
    RAISE NOTICE '=== Testing boundary pair detection ===';

    -- This test uses actual data from the boundary test objects
    -- Skip if no boundary objects exist
    SELECT COUNT(*) INTO pair_count
    FROM diaobject
    WHERE diaobjectid >= 92000000 AND diaobjectid < 92000400;

    IF pair_count < 18 THEN
        RAISE NOTICE 'SKIPPED: boundary pair test (need boundary test objects)';
        RETURN;
    END IF;

    -- Test: All boundary pairs should be found using neighbors
    WITH boundary_pairs AS (
        SELECT
            d1.diaobjectid AS id1,
            d2.diaobjectid AS id2,
            matching_group(d1.rootid) AS mg1,
            matching_group(d2.rootid) AS mg2
        FROM diaobject d1
        JOIN diaobject d2 ON
            CASE
                WHEN d1.diaobjectid % 10 = 0 THEN d2.diaobjectid = d1.diaobjectid + 1
                ELSE false
            END
        WHERE d1.diaobjectid >= 92000000 AND d1.diaobjectid < 92000400
    ),
    found_pairs AS (
        SELECT id1, id2,
            mg2 = ANY(matching_group_neighbors(mg1)) AS is_found
        FROM boundary_pairs
    )
    SELECT COUNT(*) INTO pair_count
    FROM found_pairs
    WHERE is_found = true;

    PERFORM _test_assert_equals(pair_count::BIGINT, 9,
        'All 9 boundary pairs should be found using neighbors');

    RAISE NOTICE 'PASSED: boundary pair detection tests';
END $$;

-- =============================================================================
-- Test: Index existence verification
-- =============================================================================
DO $$
DECLARE
    index_exists BOOLEAN;
BEGIN
    RAISE NOTICE '=== Testing index existence ===';

    -- Check if index exists
    SELECT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_diaobject_matching_group'
    ) INTO index_exists;

    IF NOT index_exists THEN
        RAISE WARNING 'Index idx_diaobject_matching_group does not exist';
    ELSE
        RAISE NOTICE 'PASSED: Index idx_diaobject_matching_group exists';
    END IF;

    -- Note: We don't test actual index usage because with small tables
    -- PostgreSQL may choose sequential scan anyway
END $$;

-- =============================================================================
-- Cleanup test functions
-- =============================================================================
DROP FUNCTION IF EXISTS _test_assert(BOOLEAN, TEXT);
DROP FUNCTION IF EXISTS _test_assert_equals(BIGINT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS _test_assert_array_contains(BIGINT[], BIGINT, TEXT);

-- =============================================================================
-- Summary
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '============================================';
    RAISE NOTICE 'ALL TESTS COMPLETED SUCCESSFULLY';
    RAISE NOTICE '============================================';
END $$;
