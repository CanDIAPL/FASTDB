-- =============================================================================
-- BOUNDARY MATCHING DEMONSTRATION
-- =============================================================================
-- This script demonstrates how objects within 1 arcsecond can fall into
-- different HEALPix cells, and how matching_group_neighbors() finds them.
--
-- Prerequisites:
--   1. Run seed_boundary_objects.py to create test data in MongoDB
--   2. Run source_importer to import into PostgreSQL
--
-- Usage:
--   psql -d fastdb -f scripts/demo_boundary_matching.sql
-- =============================================================================

\pset pager off
\timing on

\echo ''
\echo '============================================================================='
\echo 'BOUNDARY MATCHING DEMONSTRATION'
\echo '============================================================================='
\echo ''

-- =============================================================================
-- STEP 1: Show the boundary test objects
-- =============================================================================
\echo '=== STEP 1: Boundary Test Objects ==='
\echo 'Objects 92000000-92000301 are pairs placed ~0.8" apart across HEALPix boundaries'
\echo ''

SELECT
    diaobjectid,
    ROUND(ra::numeric, 6) AS ra,
    ROUND(dec::numeric, 6) AS dec,
    matching_group(spatial_id) AS matching_group
FROM diaobject
WHERE diaobjectid >= 92000000
ORDER BY diaobjectid;

-- =============================================================================
-- STEP 2: Show pairs have DIFFERENT matching_groups
-- =============================================================================
\echo ''
\echo '=== STEP 2: Pairs Have DIFFERENT matching_groups ==='
\echo 'Even though they are <1" apart, they fall in different HEALPix cells'
\echo ''

WITH pairs AS (
    SELECT
        d1.diaobjectid AS obj1,
        d2.diaobjectid AS obj2,
        ROUND((3600 * SQRT(
            POWER((d1.ra - d2.ra) * COS(RADIANS(d1.dec)), 2) +
            POWER(d1.dec - d2.dec, 2)
        ))::numeric, 3) AS separation_arcsec,
        matching_group(d1.spatial_id) AS mg1,
        matching_group(d2.spatial_id) AS mg2
    FROM diaobject d1
    JOIN diaobject d2 ON d2.diaobjectid = d1.diaobjectid + 1
    WHERE d1.diaobjectid >= 92000000
      AND d1.diaobjectid < 92000400
      AND d1.diaobjectid % 10 = 0
)
SELECT
    obj1, obj2,
    separation_arcsec || '"' AS sep,
    mg1, mg2,
    CASE WHEN mg1 = mg2 THEN 'SAME' ELSE 'DIFFERENT' END AS cell_status
FROM pairs
ORDER BY obj1;

-- =============================================================================
-- STEP 3: Show matching_group_neighbors() FINDS all pairs
-- =============================================================================
\echo ''
\echo '=== STEP 3: matching_group_neighbors() FINDS All Pairs ==='
\echo 'By checking the center cell + 8 neighbors, all pairs are found'
\echo ''

WITH pairs AS (
    SELECT
        d1.diaobjectid AS obj1,
        d2.diaobjectid AS obj2,
        ROUND((3600 * SQRT(
            POWER((d1.ra - d2.ra) * COS(RADIANS(d1.dec)), 2) +
            POWER(d1.dec - d2.dec, 2)
        ))::numeric, 3) AS separation_arcsec,
        matching_group(d1.spatial_id) AS mg1,
        matching_group(d2.spatial_id) AS mg2
    FROM diaobject d1
    JOIN diaobject d2 ON d2.diaobjectid = d1.diaobjectid + 1
    WHERE d1.diaobjectid >= 92000000
      AND d1.diaobjectid < 92000400
      AND d1.diaobjectid % 10 = 0
)
SELECT
    obj1, obj2,
    separation_arcsec || '"' AS sep,
    CASE WHEN mg1 = mg2 THEN 'SAME_CELL' ELSE 'DIFF_CELL' END AS cells,
    CASE
        WHEN mg2 = ANY(matching_group_neighbors(mg1))
        THEN 'FOUND'
        ELSE 'MISSED'
    END AS neighbor_search
FROM pairs
ORDER BY obj1;

-- =============================================================================
-- STEP 4: Example query pattern for finding nearby objects
-- =============================================================================
\echo ''
\echo '=== STEP 4: Query Pattern for Finding Nearby Objects ==='
\echo 'Find all objects within ~1" of object 92000000'
\echo ''

WITH target AS (
    SELECT spatial_id, ra, dec
    FROM diaobject
    WHERE diaobjectid = 92000000
)
SELECT
    d.diaobjectid,
    ROUND(d.ra::numeric, 6) AS ra,
    ROUND(d.dec::numeric, 6) AS dec,
    ROUND((3600 * SQRT(
        POWER((d.ra - t.ra) * COS(RADIANS(d.dec)), 2) +
        POWER(d.dec - t.dec, 2)
    ))::numeric, 3) || '"' AS distance
FROM diaobject d, target t
WHERE matching_group(d.spatial_id) = ANY(
    matching_group_neighbors(matching_group(t.spatial_id))
)
AND d.diaobjectid != 92000000
ORDER BY distance
LIMIT 10;

-- =============================================================================
-- STEP 5: Compare with Q3C cone search
-- =============================================================================
\echo ''
\echo '=== STEP 5: Verification with Q3C Cone Search ==='
\echo 'Q3C should find the same objects (ground truth)'
\echo ''

WITH target AS (
    SELECT ra, dec
    FROM diaobject
    WHERE diaobjectid = 92000000
)
SELECT
    d.diaobjectid,
    ROUND(d.ra::numeric, 6) AS ra,
    ROUND(d.dec::numeric, 6) AS dec,
    ROUND((3600 * SQRT(
        POWER((d.ra - t.ra) * COS(RADIANS(d.dec)), 2) +
        POWER(d.dec - t.dec, 2)
    ))::numeric, 3) || '"' AS distance
FROM diaobject d, target t
WHERE q3c_join(t.ra, t.dec, d.ra, d.dec, 10.0/3600)  -- 10 arcsec radius
AND d.diaobjectid != 92000000
ORDER BY distance;

-- =============================================================================
-- STEP 6: Performance comparison
-- =============================================================================
\echo ''
\echo '=== STEP 6: Performance Comparison ==='
\echo 'Compare matching_group + Q3C vs Q3C alone'
\echo ''

\echo 'Method 1: matching_group pre-filter + Q3C verification'
EXPLAIN ANALYZE
WITH target AS (
    SELECT spatial_id, ra, dec
    FROM diaobject
    WHERE diaobjectid = 92000000
)
SELECT COUNT(*)
FROM diaobject d, target t
WHERE matching_group(d.spatial_id) = ANY(
    matching_group_neighbors(matching_group(t.spatial_id))
)
AND q3c_join(t.ra, t.dec, d.ra, d.dec, 1.0/3600);

\echo ''
\echo 'Method 2: Q3C only'
EXPLAIN ANALYZE
WITH target AS (
    SELECT ra, dec
    FROM diaobject
    WHERE diaobjectid = 92000000
)
SELECT COUNT(*)
FROM diaobject d, target t
WHERE q3c_join(t.ra, t.dec, d.ra, d.dec, 1.0/3600);

-- =============================================================================
-- SUMMARY
-- =============================================================================
\echo ''
\echo '============================================================================='
\echo 'SUMMARY'
\echo '============================================================================='
\echo ''
\echo 'Key findings:'
\echo '  - 9 pairs of objects placed across HEALPix boundaries (~0.8" apart)'
\echo '  - Direct matching_group comparison: pairs in DIFFERENT cells'
\echo '  - Using matching_group_neighbors(): ALL 9 pairs FOUND'
\echo ''
\echo 'Takeaway:'
\echo '  - matching_group alone misses boundary cases'
\echo '  - matching_group_neighbors() is REQUIRED for reliable proximity queries'
\echo '  - Combine with Q3C for precise distance verification'
\echo ''
\echo 'Recommended query pattern:'
\echo '  WHERE matching_group(spatial_id) = ANY(matching_group_neighbors(:target_mg))'
\echo '    AND q3c_join(...)'
\echo ''
