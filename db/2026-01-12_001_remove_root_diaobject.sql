-- Migration: Remove root_diaobject table
--
-- With spatial_id, rootid values are deterministic UUIDs encoding position.
-- The root_diaobject lookup table is no longer needed as a unique object registry.
-- Spectrum tables keep their root_diaobject_id column but without FK constraint.
--
-- Prerequisites:
--   - 2026-01-07_001_procver_compact_id.sql (spatial_group function and index)

-- Step 1: Drop FK constraint from diaobject.rootid
-- Original constraint from 2025-07-21_001_visit_primkey.sql
ALTER TABLE diaobject DROP CONSTRAINT IF EXISTS diaobject_root_fkey;

-- Step 2: Drop FK constraints from spectrum tables
-- Original constraints from 2025-04-25-spectrumcycle.sql
ALTER TABLE spectruminfo DROP CONSTRAINT IF EXISTS fk_spectruminfo_root_diaobject;
ALTER TABLE wantedspectra DROP CONSTRAINT IF EXISTS fk_wantedspectra_root_diaobject;
ALTER TABLE plannedspectra DROP CONSTRAINT IF EXISTS fk_plannedspectra_root_diaobject;

-- Step 3: Drop the root_diaobject table
-- Original table from 2025-02-18_001_init.sql
DROP TABLE IF EXISTS root_diaobject;

-- Step 4: Ensure spatial_group index exists (may already exist from previous migration)
-- This is the replacement for root_diaobject lookups
CREATE INDEX IF NOT EXISTS idx_diaobject_spatial_group
ON diaobject(spatial_group(rootid));

-- Step 5: Add comments explaining the change
COMMENT ON COLUMN diaobject.rootid IS
    'Spatial ID (deterministic UUID encoding position). Use spatial_group(rootid) for grouping queries.';

COMMENT ON COLUMN spectruminfo.root_diaobject_id IS
    'Spatial ID of the target object. No longer references root_diaobject table.';

COMMENT ON COLUMN wantedspectra.root_diaobject_id IS
    'Spatial ID of the wanted object. No longer references root_diaobject table.';

COMMENT ON COLUMN plannedspectra.root_diaobject_id IS
    'Spatial ID of the planned object. No longer references root_diaobject table.';
