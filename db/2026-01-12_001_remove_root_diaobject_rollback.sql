-- Rollback: Recreate root_diaobject table and constraints
--
-- EMERGENCY USE ONLY: This script recreates the root_diaobject table
-- and FK constraints that were removed by 2026-01-12_001_remove_root_diaobject.sql
--
-- WARNING: This will NOT restore the original root_diaobject data.
-- You must repopulate the table from existing rootid values in diaobject
-- and root_diaobject_id values in spectrum tables.

-- Step 1: Recreate the root_diaobject table
-- Original definition from 2025-02-18_001_init.sql
CREATE TABLE IF NOT EXISTS root_diaobject(
  id UUID NOT NULL PRIMARY KEY
);

COMMENT ON TABLE root_diaobject IS
    'Unique astronomical objects across all data releases (restored for rollback)';

-- Step 2: Populate from existing rootid values
-- This ensures FK constraints can be added
INSERT INTO root_diaobject (id)
SELECT DISTINCT rootid FROM diaobject
WHERE rootid IS NOT NULL
ON CONFLICT DO NOTHING;

-- Also populate from spectrum tables
INSERT INTO root_diaobject (id)
SELECT DISTINCT root_diaobject_id FROM spectruminfo
WHERE root_diaobject_id IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO root_diaobject (id)
SELECT DISTINCT root_diaobject_id FROM wantedspectra
WHERE root_diaobject_id IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO root_diaobject (id)
SELECT DISTINCT root_diaobject_id FROM plannedspectra
WHERE root_diaobject_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Step 3: Recreate FK constraint on diaobject.rootid
-- Original from 2025-07-21_001_visit_primkey.sql
ALTER TABLE diaobject ADD CONSTRAINT diaobject_root_fkey
  FOREIGN KEY (rootid) REFERENCES root_diaobject(id);

-- Step 4: Recreate FK constraints on spectrum tables
-- Original from 2025-04-25-spectrumcycle.sql
ALTER TABLE spectruminfo ADD CONSTRAINT fk_spectruminfo_root_diaobject
  FOREIGN KEY (root_diaobject_id) REFERENCES root_diaobject(id) ON DELETE RESTRICT;

ALTER TABLE wantedspectra ADD CONSTRAINT fk_wantedspectra_root_diaobject
  FOREIGN KEY (root_diaobject_id) REFERENCES root_diaobject(id) ON DELETE RESTRICT;

ALTER TABLE plannedspectra ADD CONSTRAINT fk_plannedspectra_root_diaobject
  FOREIGN KEY (root_diaobject_id) REFERENCES root_diaobject(id) ON DELETE RESTRICT;

-- Step 5: Restore original comments
COMMENT ON COLUMN diaobject.rootid IS 'UUID of the root unique diaobject of this object';
