-- Migration: Make spatial_id the primary key and dedup key for diaobject
--
-- Motivation: Multi-writer source importers need ON CONFLICT (spatial_id) DO NOTHING
-- so concurrent importers processing the same alert resolve conflicts via the
-- deterministic spatial_id. The same diaobjectid can appear with different spatial_ids
-- across processing versions, so diaobjectid alone is not unique.
--
-- Prerequisites:
--   - 2026-01-12_001_add_spatial_id.sql (spatial_id column exists)
--   - All existing diaobject rows must have spatial_id populated
--
-- Changes:
--   diaobject: spatial_id becomes PK; (diaobjectid, spatial_id) becomes UNIQUE (FK target)
--   diasource: adds spatial_id column; FK changes to (diaobjectid, spatial_id)
--   diaforcedsource: adds spatial_id column; FK changes to (diaobjectid, spatial_id)
--   root_diaobject, rootid column: unchanged

BEGIN;

-- ============================================================
-- Step 1: Drop dependent FKs on source tables FIRST
--         (they reference unique_diaobjectid which we need to drop)
-- ============================================================

ALTER TABLE diasource DROP CONSTRAINT fk_diasource_diaobjectid;
ALTER TABLE diaforcedsource DROP CONSTRAINT fk_diaforcedsource_diaobjectid;

-- ============================================================
-- Step 2: diaobject — make spatial_id NOT NULL and new PK
-- ============================================================

-- Fail fast if any NULLs exist
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM diaobject WHERE spatial_id IS NULL) THEN
    RAISE EXCEPTION 'Cannot migrate: diaobject has rows with NULL spatial_id. Backfill first.';
  END IF;
END $$;

ALTER TABLE diaobject ALTER COLUMN spatial_id SET NOT NULL;

-- Drop the old PK and unique constraint (now safe — no dependent FKs)
ALTER TABLE diaobject DROP CONSTRAINT diaobject_pkey;
ALTER TABLE diaobject DROP CONSTRAINT unique_diaobjectid;

-- New PK on spatial_id (deterministic, used for dedup)
ALTER TABLE diaobject ADD PRIMARY KEY (spatial_id);

-- Composite unique needed as FK target for source tables
ALTER TABLE diaobject ADD CONSTRAINT uq_diaobject_diaobjectid_spatial_id
  UNIQUE (diaobjectid, spatial_id);

-- ============================================================
-- Step 3: diasource — add spatial_id, re-point FK
-- ============================================================

ALTER TABLE diasource ADD COLUMN spatial_id UUID;

-- Populate spatial_id from diaobject for existing rows
UPDATE diasource ds
SET spatial_id = o.spatial_id
FROM diaobject o
WHERE o.diaobjectid = ds.diaobjectid;

-- New FK referencing the composite unique
ALTER TABLE diasource ADD CONSTRAINT fk_diasource_diaobject
  FOREIGN KEY (diaobjectid, spatial_id)
  REFERENCES diaobject(diaobjectid, spatial_id)
  ON DELETE CASCADE
  DEFERRABLE INITIALLY IMMEDIATE;

CREATE INDEX idx_diasource_spatial_id ON diasource(spatial_id);

-- ============================================================
-- Step 4: diaforcedsource — add spatial_id, re-point FK
-- ============================================================

ALTER TABLE diaforcedsource ADD COLUMN spatial_id UUID;

-- Populate spatial_id from diaobject for existing rows
UPDATE diaforcedsource dfs
SET spatial_id = o.spatial_id
FROM diaobject o
WHERE o.diaobjectid = dfs.diaobjectid;

-- New FK referencing the composite unique
ALTER TABLE diaforcedsource ADD CONSTRAINT fk_diaforcedsource_diaobject
  FOREIGN KEY (diaobjectid, spatial_id)
  REFERENCES diaobject(diaobjectid, spatial_id)
  ON DELETE CASCADE
  DEFERRABLE INITIALLY IMMEDIATE;

CREATE INDEX idx_diaforcedsource_spatial_id ON diaforcedsource(spatial_id);

COMMIT;
