# ADR: Phase 1 — Additive spatial_id Column for Multi-Writer Deduplication

**Status:** Implemented
**Date:** 2026-02-04
**Author:** Carlo Costantini

## Context

The `source_importer.py` module imports diaobject, diasource, and diaforcedsource records
from MongoDB into PostgreSQL. The existing `rootid` system uses:

1. **Cone search matching** (`q3c_radial_query`) to link new objects to existing root objects
2. **Random UUID generation** (`gen_random_uuid()`) for unmatched objects
3. **`root_diaobject` table** to maintain referential integrity

This works for single-writer scenarios but has limitations for multi-writer deployments:

- **Non-deterministic**: Same object imported by different writers gets different random UUIDs
- **Race conditions**: Concurrent imports can create duplicate `root_diaobject` entries
- **Conflict on replication**: Random UUIDs from different masters collide

### Goal

Enable multiple `source_importer` instances to run concurrently without conflicts, while
preserving backward compatibility with existing rootid-based queries and the `root_diaobject`
table.

## Decision

Add `spatial_id` as a **separate column** alongside `rootid`, not replacing it:

1. **Add `spatial_id` column** to `diaobject`, `diasource`, `diaforcedsource`
2. **Make `spatial_id` the primary key** for `diaobject` (enables `ON CONFLICT DO NOTHING`)
3. **Keep `rootid` and `root_diaobject`** unchanged for backward compatibility
4. **Keep cone-search matching** for rootid assignment (unchanged behavior)

### Why Additive (Not Replacement)?

1. **Smooth migration**: Existing queries using `rootid` continue to work
2. **Validation period**: Can compare rootid grouping vs spatial_group() in production
3. **Rollback safety**: If spatial_id has issues, rootid is still there
4. **Incremental complexity**: Smaller change, easier to review and test

### Design Principles

1. **Deterministic dedup key**: `spatial_id` is computed from (ra, dec, mjd, procver, data_release)
2. **Idempotent imports**: `ON CONFLICT (spatial_id) DO NOTHING` silently skips duplicates
3. **Backward compatible**: All existing rootid-based queries unchanged
4. **Additive only**: No removal of existing columns, tables, or constraints

## Schema Changes

### Migration: `db/2026-02-04_001_spatial_id_primary_key.sql`

```sql
-- Step 1: Drop dependent FKs (they reference unique_diaobjectid)
ALTER TABLE diasource DROP CONSTRAINT fk_diasource_diaobjectid;
ALTER TABLE diaforcedsource DROP CONSTRAINT fk_diaforcedsource_diaobjectid;

-- Step 2: diaobject — make spatial_id the PK
ALTER TABLE diaobject ALTER COLUMN spatial_id SET NOT NULL;
ALTER TABLE diaobject DROP CONSTRAINT diaobject_pkey;
ALTER TABLE diaobject DROP CONSTRAINT unique_diaobjectid;
ALTER TABLE diaobject ADD PRIMARY KEY (spatial_id);
ALTER TABLE diaobject ADD CONSTRAINT uq_diaobject_diaobjectid_spatial_id
  UNIQUE (diaobjectid, spatial_id);

-- Step 3: diasource — add spatial_id, re-point FK
ALTER TABLE diasource ADD COLUMN spatial_id UUID;
UPDATE diasource ds SET spatial_id = o.spatial_id
FROM diaobject o WHERE o.diaobjectid = ds.diaobjectid;
ALTER TABLE diasource ADD CONSTRAINT fk_diasource_diaobject
  FOREIGN KEY (diaobjectid, spatial_id)
  REFERENCES diaobject(diaobjectid, spatial_id)
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- Step 4: diaforcedsource — same pattern
ALTER TABLE diaforcedsource ADD COLUMN spatial_id UUID;
UPDATE diaforcedsource dfs SET spatial_id = o.spatial_id
FROM diaobject o WHERE o.diaobjectid = dfs.diaobjectid;
ALTER TABLE diaforcedsource ADD CONSTRAINT fk_diaforcedsource_diaobject
  FOREIGN KEY (diaobjectid, spatial_id)
  REFERENCES diaobject(diaobjectid, spatial_id)
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;
```

### Unchanged

- `rootid` column on `diaobject` — kept, still populated via cone search
- `root_diaobject` table — kept, still receives new entries
- FK from `diaobject.rootid` to `root_diaobject.id` — kept

## source_importer.py Changes

### `__init__` Signature

```python
def __init__(self, base_processing_version, object_base_processing_version,
             object_match_radius=1., procver=0, data_release=0):
```

New parameters:
- `procver`: Processing version compact ID for spatial_id encoding (default: 0)
- `data_release`: Data release identifier (default: 0 = realtime)

Note: `object_match_radius` is **kept** because cone-search matching is still used for rootid.

### `import_objects_from_collection` Workflow

The method now does **both** rootid assignment (unchanged) and spatial_id generation (new):

1. Read objects from MongoDB into temp table
2. Filter to new objects only
3. **Link to existing rootids via cone search** (unchanged)
4. **Generate random UUIDs for unmatched** (unchanged)
5. **Insert into root_diaobject** (unchanged)
6. **Generate spatial_id for each object** (NEW)
7. **Insert with `ON CONFLICT (spatial_id) DO NOTHING`** (NEW)

```python
# Step 6: Generate spatial_id (NEW)
cursor.execute("SELECT diaobjectid, ra, dec, validitystartmjdtai FROM temp_new_diaobject")
rows = cursor.fetchall()
for diaobjectid, ra, dec, mjd in rows:
    spatial_id = generate_spatial_id(ra, dec, mjd, self.procver, self.data_release)
    cursor.execute("UPDATE temp_new_diaobject SET spatial_id = %s WHERE diaobjectid = %s",
                   (str(spatial_id), diaobjectid))

# Step 7: Insert with dedup on spatial_id (NEW)
cursor.execute("INSERT INTO diaobject (SELECT * FROM temp_new_diaobject) "
               "ON CONFLICT (spatial_id) DO NOTHING")
```

### Source Table Imports

`import_sources_from_collection`, `import_prvsources_from_collection`, and
`import_prvforcedsources_from_collection` now populate `spatial_id` by joining to `diaobject`:

```python
cursor.execute("UPDATE temp_diasource_import tsi "
               "SET spatial_id = o.spatial_id "
               "FROM diaobject o "
               "WHERE o.diaobjectid = tsi.diaobjectid "
               "  AND o.base_procver_id = tsi.base_procver_id")
```

### Temp Table Handling

`_read_mongo_fields` drops NOT NULL on `spatial_id` for temp tables (it gets populated after
the COPY from MongoDB):

```python
if liketable == 'diaobject':
    pqcursor.execute(f"ALTER TABLE {temptable} ALTER COLUMN rootid DROP NOT NULL")
    pqcursor.execute(f"ALTER TABLE {temptable} ALTER COLUMN spatial_id DROP NOT NULL")
elif liketable in ('diasource', 'diaforcedsource'):
    pqcursor.execute(f"ALTER TABLE {temptable} ALTER COLUMN spatial_id DROP NOT NULL")
```

## Multi-Writer Behavior

With `spatial_id` as the primary key and `ON CONFLICT DO NOTHING`:

```
Writer A: generate_spatial_id(ra, dec, mjd, 0, 0) → UUID-X
Writer B: generate_spatial_id(ra, dec, mjd, 0, 0) → UUID-X  (SAME!)

Writer A: INSERT ... ON CONFLICT (spatial_id) DO NOTHING → inserted
Writer B: INSERT ... ON CONFLICT (spatial_id) DO NOTHING → skipped (no error)
```

Both writers produce identical `spatial_id` for the same observation. The second writer's
insert is silently skipped. No conflicts, no race conditions.

## Consequences

### Positive

1. **Multi-writer safe**: Concurrent imports work without conflicts
2. **Deterministic**: Same input always produces same spatial_id
3. **Backward compatible**: All rootid-based queries unchanged
4. **Idempotent**: Re-importing same data is safe
5. **Incremental**: Can validate before removing legacy infrastructure

### Negative

1. **Dual systems**: Both rootid and spatial_id exist, potential confusion
2. **Extra column**: Storage overhead for spatial_id on source tables
3. **Migration required**: Existing deployments need schema migration

### Neutral

1. **rootid still non-deterministic**: Different writers may assign different rootids
   (but spatial_id handles dedup, so this doesn't cause conflicts)

## Verification

The `scripts/multiwriter_demo/` directory contains an end-to-end test:

```bash
# Run the demo
PYTHONPATH=/fastdb python /fastdb/scripts/multiwriter_demo/run_demo.py

# Verification checks:
# 1. No duplicate spatial_ids in database
# 2. Deterministic regeneration (same inputs → same spatial_id)
# 3. spatial_group() works correctly
# 4. Object counts match expectations
# 5. root_diaobject FK integrity maintained
```

## Future Work (Phase 2)

Once Phase 1 is validated in production, Phase 2 will:

1. Remove cone-search matching from import path
2. Remove `root_diaobject` table
3. Potentially rename `spatial_id` to `rootid` or remove `rootid` column
4. Update spectrum tables to use `spatial_group()` instead of FK joins

See [ADR_SPATIAL_ID_PHASE2_FULL_REPLACEMENT.md](./ADR_SPATIAL_ID_PHASE2_FULL_REPLACEMENT.md)
for the Phase 2 design.

## References

- `db/2026-02-04_001_spatial_id_primary_key.sql` — Schema migration
- `src/services/source_importer.py` — Implementation
- `src/spatial_id.py` — spatial_id generation
- `scripts/multiwriter_demo/` — Verification scripts
- `scripts/README.md` — Script documentation
