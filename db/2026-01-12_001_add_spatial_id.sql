-- Migration: Add spatial_id column to diaobject
--
-- Adds a new spatial_id column (deterministic UUID encoding position) alongside
-- the existing rootid (random UUID with FK to root_diaobject).
-- root_diaobject table and all FK constraints remain intact.
--
-- Prerequisites:
--   - 2026-01-07_001_procver_compact_id.sql (spatial_group function)

-- Step 1: Add spatial_id column to diaobject
ALTER TABLE diaobject ADD COLUMN IF NOT EXISTS spatial_id UUID;

COMMENT ON COLUMN diaobject.spatial_id IS
    'Deterministic UUID encoding (ra, dec, mjd, procver, data_release). '
    'Use spatial_group(spatial_id) for position-based grouping queries.';

-- Step 2: Create spatial_group index on the new spatial_id column
CREATE INDEX IF NOT EXISTS idx_diaobject_spatial_group
ON diaobject(spatial_group(spatial_id));

COMMENT ON INDEX idx_diaobject_spatial_group IS
    'Index for efficient spatial grouping queries using spatial_group(spatial_id). '
    'Objects at the same sky position (within ~0.05") share the same group prefix.';
