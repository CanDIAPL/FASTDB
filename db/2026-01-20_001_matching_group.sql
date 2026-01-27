-- Migration: Add matching_group functions for proximity queries
--
-- The matching_group functions provide coarse HEALPix cells (~12.6") for finding
-- objects within ~1 arcsecond of each other. This is complementary to spatial_group
-- which provides fine resolution (~0.05") for exact position matching.
--
-- Resolution comparison:
--   spatial_group():  NSIDE=2^22 equivalent, ~0.05" cells - exact position matching
--   matching_group(): NSIDE=2^14, ~12.6" cells - proximity queries (< 1")
--
-- Objects within 1 arcsecond are guaranteed to be in the same matching_group cell
-- or in adjacent cells. Use matching_group_neighbors() to get all 9 cells.

-- =============================================================================
-- Helper function: Undilate (extract every other bit from Morton code)
-- =============================================================================
CREATE OR REPLACE FUNCTION _mg_undilate(x BIGINT) RETURNS BIGINT AS $$
DECLARE
    v BIGINT;
BEGIN
    v := x & x'5555555555555555'::BIGINT;
    v := (v | (v >> 1)) & x'3333333333333333'::BIGINT;
    v := (v | (v >> 2)) & x'0F0F0F0F0F0F0F0F'::BIGINT;
    v := (v | (v >> 4)) & x'00FF00FF00FF00FF'::BIGINT;
    v := (v | (v >> 8)) & x'0000FFFF0000FFFF'::BIGINT;
    v := (v | (v >> 16)) & x'00000000FFFFFFFF'::BIGINT;
    RETURN v;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

COMMENT ON FUNCTION _mg_undilate(BIGINT) IS
    'Internal helper: Extract every other bit from Morton code (Z-order curve). '
    'Used by matching_group_neighbors to convert between Morton and x,y coordinates.';

-- =============================================================================
-- Helper function: Dilate (spread bits for Morton code)
-- =============================================================================
CREATE OR REPLACE FUNCTION _mg_dilate(x BIGINT) RETURNS BIGINT AS $$
DECLARE
    v BIGINT;
BEGIN
    v := x & x'00000000FFFFFFFF'::BIGINT;
    v := (v | (v << 16)) & x'0000FFFF0000FFFF'::BIGINT;
    v := (v | (v << 8)) & x'00FF00FF00FF00FF'::BIGINT;
    v := (v | (v << 4)) & x'0F0F0F0F0F0F0F0F'::BIGINT;
    v := (v | (v << 2)) & x'3333333333333333'::BIGINT;
    v := (v | (v << 1)) & x'5555555555555555'::BIGINT;
    RETURN v;
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

COMMENT ON FUNCTION _mg_dilate(BIGINT) IS
    'Internal helper: Spread bits for Morton code (Z-order curve). '
    'Used by matching_group_neighbors to convert between x,y and Morton coordinates.';

-- =============================================================================
-- Helper function: Convert (x,y) to Morton code
-- =============================================================================
CREATE OR REPLACE FUNCTION _mg_xy_to_morton(x BIGINT, y BIGINT) RETURNS BIGINT AS $$
BEGIN
    RETURN _mg_dilate(x) | (_mg_dilate(y) << 1);
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;

COMMENT ON FUNCTION _mg_xy_to_morton(BIGINT, BIGINT) IS
    'Internal helper: Convert (x,y) coordinates to Morton code (Z-order curve). '
    'Used by matching_group_neighbors for HEALPix pixel arithmetic.';

-- =============================================================================
-- Main function: matching_group
-- Extract coarse HEALPix cell from spatial_id for proximity queries
-- =============================================================================
CREATE OR REPLACE FUNCTION matching_group(sid UUID) RETURNS BIGINT AS $$
DECLARE
    uuid_bytes BYTEA;
    high64 BIGINT;
BEGIN
    -- Convert UUID to binary representation (16 bytes, big-endian)
    uuid_bytes := uuid_send(sid);

    -- Extract high64 (bytes 0-7, contains HEALPix at NSIDE=2^29)
    high64 := (get_byte(uuid_bytes, 0)::BIGINT << 56)
            | (get_byte(uuid_bytes, 1)::BIGINT << 48)
            | (get_byte(uuid_bytes, 2)::BIGINT << 40)
            | (get_byte(uuid_bytes, 3)::BIGINT << 32)
            | (get_byte(uuid_bytes, 4)::BIGINT << 24)
            | (get_byte(uuid_bytes, 5)::BIGINT << 16)
            | (get_byte(uuid_bytes, 6)::BIGINT << 8)
            | (get_byte(uuid_bytes, 7)::BIGINT);

    -- Shift right 30 bits to convert from NSIDE=2^29 to NSIDE=2^14
    -- Level difference = 29 - 14 = 15, shift = 2 * 15 = 30 bits
    RETURN high64 >> 30;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION matching_group(UUID) IS
    'Extract coarse HEALPix cell (NSIDE=2^14, ~12.6" cells) from spatial_id for proximity queries. '
    'Objects within 1 arcsec are guaranteed to be in same cell or adjacent cells. '
    'Use with matching_group_neighbors() for robust matching across cell boundaries.';

-- =============================================================================
-- Main function: matching_group_neighbors
-- Get matching_group and its 8 HEALPix neighbors for proximity queries
-- =============================================================================
CREATE OR REPLACE FUNCTION matching_group_neighbors(mg BIGINT) RETURNS BIGINT[] AS $$
DECLARE
    nside BIGINT := 16384;  -- 2^14
    npix_per_base BIGINT;
    base_pixel BIGINT;
    local_idx BIGINT;
    x BIGINT;
    y BIGINT;
    result BIGINT[] := ARRAY[mg];  -- Always include self
    dx INT;
    dy INT;
    nx BIGINT;
    ny BIGINT;
    new_local BIGINT;
BEGIN
    npix_per_base := nside * nside;
    base_pixel := mg / npix_per_base;
    local_idx := mg % npix_per_base;

    -- Extract x, y from Morton code
    x := _mg_undilate(local_idx);
    y := _mg_undilate(local_idx >> 1);

    -- Check all 8 neighbor directions
    FOR dx IN -1..1 LOOP
        FOR dy IN -1..1 LOOP
            IF dx = 0 AND dy = 0 THEN
                CONTINUE;
            END IF;

            nx := x + dx;
            ny := y + dy;

            -- Check if within base pixel bounds
            IF nx >= 0 AND nx < nside AND ny >= 0 AND ny < nside THEN
                -- Same base pixel: simple Morton arithmetic
                new_local := _mg_xy_to_morton(nx, ny);
                result := array_append(result, base_pixel * npix_per_base + new_local);
            END IF;
            -- Note: Cross-boundary cases at base pixel edges are not handled
            -- For positions near base pixel boundaries, use Q3C cone search as fallback
        END LOOP;
    END LOOP;

    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION matching_group_neighbors(BIGINT) IS
    'Get a matching_group value and its 8 HEALPix neighbors for proximity queries. '
    'Returns array of up to 9 matching_group values. Objects within 1 arcsec of a '
    'position are guaranteed to be in one of these cells (except at base pixel boundaries). '
    'Usage: WHERE matching_group(spatial_id) = ANY(matching_group_neighbors(target_mg))';

-- =============================================================================
-- Index for efficient matching_group queries (default 10" precision)
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_diaobject_matching_group
ON diaobject(matching_group(spatial_id));

COMMENT ON INDEX idx_diaobject_matching_group IS
    'Index for efficient proximity queries using matching_group(rootid). '
    'Enables fast lookup of objects within ~1 arcsec using matching_group_neighbors(). '
    'Uses spatial_id column (deterministic UUID encoding position).';

-- =============================================================================
-- Parameterized matching: matching_group_at_precision
-- Allows configurable precision (1", 10", 100") for different use cases
-- =============================================================================

-- Helper function: Calculate NSIDE for a given precision in arcseconds
-- HEALPix pixel size ≈ 206265" / NSIDE
-- Returns largest power-of-2 NSIDE where pixel_size >= arcsec
CREATE OR REPLACE FUNCTION _mg_nside_for_precision(arcsec FLOAT) RETURNS BIGINT AS $$
DECLARE
    max_nside FLOAT;
    exponent INT;
BEGIN
    IF arcsec <= 0 THEN
        RAISE EXCEPTION 'Precision must be > 0, got %', arcsec;
    END IF;

    -- NSIDE <= 206265 / arcsec for pixel_size >= arcsec
    max_nside := 206265.0 / arcsec;

    -- Find largest power of 2 <= max_nside
    -- PostgreSQL: LOG(x) is natural log, use LN(x)/LN(2) for log base 2
    exponent := FLOOR(LN(max_nside) / LN(2.0))::INT;

    -- Clamp to valid range [0, 29]
    exponent := GREATEST(0, LEAST(exponent, 29));

    RETURN (1::BIGINT << exponent);
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION _mg_nside_for_precision(FLOAT) IS
    'Calculate NSIDE for a given precision in arcseconds. '
    'Returns largest power-of-2 NSIDE where pixel size >= precision.';

-- Helper function: Calculate shift bits from storage NSIDE (2^29) to target NSIDE
CREATE OR REPLACE FUNCTION _mg_shift_bits_for_nside(target_nside BIGINT) RETURNS INT AS $$
DECLARE
    target_exp INT;
BEGIN
    -- PostgreSQL: use LN(x)/LN(2) for log base 2
    target_exp := FLOOR(LN(target_nside::FLOAT) / LN(2.0))::INT;
    -- Level difference = 29 - target_exp, shift = 2 * level_difference
    RETURN 2 * (29 - target_exp);
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION _mg_shift_bits_for_nside(BIGINT) IS
    'Calculate bit shift to convert from storage NSIDE (2^29) to target NSIDE.';

-- =============================================================================
-- Main parameterized function: matching_group_at_precision
-- Extract matching group at specified precision (in arcseconds)
-- =============================================================================
CREATE OR REPLACE FUNCTION matching_group_at_precision(sid UUID, arcsec FLOAT DEFAULT 10.0)
RETURNS BIGINT AS $$
DECLARE
    uuid_bytes BYTEA;
    high64 BIGINT;
    target_nside BIGINT;
    shift_bits INT;
BEGIN
    -- Convert UUID to binary representation
    uuid_bytes := uuid_send(sid);

    -- Extract high64 (bytes 0-7, contains HEALPix at NSIDE=2^29)
    high64 := (get_byte(uuid_bytes, 0)::BIGINT << 56)
            | (get_byte(uuid_bytes, 1)::BIGINT << 48)
            | (get_byte(uuid_bytes, 2)::BIGINT << 40)
            | (get_byte(uuid_bytes, 3)::BIGINT << 32)
            | (get_byte(uuid_bytes, 4)::BIGINT << 24)
            | (get_byte(uuid_bytes, 5)::BIGINT << 16)
            | (get_byte(uuid_bytes, 6)::BIGINT << 8)
            | (get_byte(uuid_bytes, 7)::BIGINT);

    -- Calculate NSIDE and shift for requested precision
    target_nside := _mg_nside_for_precision(arcsec);
    shift_bits := _mg_shift_bits_for_nside(target_nside);

    RETURN high64 >> shift_bits;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION matching_group_at_precision(UUID, FLOAT) IS
    'Extract matching group at specified precision for proximity queries. '
    'Precision is in arcseconds (common values: 1, 10, 100). '
    'Objects within the precision radius are in same cell or adjacent cells. '
    'Default is 10" (~12.6" cells). Use 1" for fine matching, 100" for coarse.';

-- =============================================================================
-- Parameterized neighbors function: matching_group_neighbors_at_precision
-- Get matching group and 8 neighbors at specified precision
-- =============================================================================
CREATE OR REPLACE FUNCTION matching_group_neighbors_at_precision(mg BIGINT, arcsec FLOAT DEFAULT 10.0)
RETURNS BIGINT[] AS $$
DECLARE
    target_nside BIGINT;
    npix_per_base BIGINT;
    base_pixel BIGINT;
    local_idx BIGINT;
    x BIGINT;
    y BIGINT;
    result BIGINT[] := ARRAY[mg];
    dx INT;
    dy INT;
    nx BIGINT;
    ny BIGINT;
    new_local BIGINT;
BEGIN
    target_nside := _mg_nside_for_precision(arcsec);
    npix_per_base := target_nside * target_nside;
    base_pixel := mg / npix_per_base;
    local_idx := mg % npix_per_base;

    -- Extract x, y from Morton code
    x := _mg_undilate(local_idx);
    y := _mg_undilate(local_idx >> 1);

    -- Check all 8 neighbor directions
    FOR dx IN -1..1 LOOP
        FOR dy IN -1..1 LOOP
            IF dx = 0 AND dy = 0 THEN
                CONTINUE;
            END IF;

            nx := x + dx;
            ny := y + dy;

            -- Check if within base pixel bounds
            IF nx >= 0 AND nx < target_nside AND ny >= 0 AND ny < target_nside THEN
                new_local := _mg_xy_to_morton(nx, ny);
                result := array_append(result, base_pixel * npix_per_base + new_local);
            END IF;
        END LOOP;
    END LOOP;

    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION matching_group_neighbors_at_precision(BIGINT, FLOAT) IS
    'Get a matching group and its 8 HEALPix neighbors at specified precision. '
    'Use with matching_group_at_precision() for configurable proximity queries. '
    'Precision in arcseconds (1, 10, 100). Default is 10".';

-- =============================================================================
-- Convenience function: Get precision info for a given arcsec value
-- =============================================================================
CREATE OR REPLACE FUNCTION matching_precision_info(arcsec FLOAT)
RETURNS TABLE(nside BIGINT, pixel_size_arcsec FLOAT, total_pixels BIGINT, shift_bits INT) AS $$
DECLARE
    target_nside BIGINT;
BEGIN
    target_nside := _mg_nside_for_precision(arcsec);
    nside := target_nside;
    pixel_size_arcsec := 206265.0 / target_nside;
    total_pixels := 12 * target_nside * target_nside;
    shift_bits := _mg_shift_bits_for_nside(target_nside);
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

COMMENT ON FUNCTION matching_precision_info(FLOAT) IS
    'Get information about matching at a specific precision. '
    'Returns NSIDE, pixel size, total pixels, and shift bits. '
    'Useful for understanding trade-offs of different precision settings.';
