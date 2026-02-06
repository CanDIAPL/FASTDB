# Spatial ID and Spectrum Queries

This document explains how `spatial_id` integrates with spectrum workflows, covering both the current Phase 1 implementation (additive spatial_id) and the proposed Phase 2 migration (full replacement).

## Current State (Phase 1)

**Status:** Implemented as of 2026-02-04

In Phase 1, `spatial_id` was added as a **separate column** alongside `rootid`. The spectrum tables and `root_diaobject` table are **unchanged**.

### Schema Summary

```
diaobject
├── spatial_id UUID (PK)     -- deterministic, from (ra, dec, mjd, procver, data_release)
├── diaobjectid BIGINT       -- unique per (object, base_procver_id)
├── rootid UUID              -- random, from cone search + gen_random_uuid()
└── rootid FK → root_diaobject(id)

spectrum tables (spectruminfo, wantedspectra, plannedspectra)
└── root_diaobject_id UUID FK → root_diaobject(id)  -- UNCHANGED
```

### Current Query Pattern

The spectrum code (`src/spectrum.py`) still uses rootid-based joins:

```sql
-- spectrum.py:223 (current)
INNER JOIN diaobject o ON t.root_diaobject_id=o.rootid
```

This requires:
1. A separate `root_diaobject` lookup table
2. Random UUIDs assigned at ingest time (multi-master conflict risk)
3. Positional cone searches to find the initial `root_diaobject_id`

### What Phase 1 Enables (No Spectrum Changes Needed)

Phase 1 gives us multi-writer safe imports via `ON CONFLICT (spatial_id) DO NOTHING`:

```python
# Two writers importing same object
Writer A: generate_spatial_id(ra, dec, mjd, 0, 0) → UUID-X
Writer B: generate_spatial_id(ra, dec, mjd, 0, 0) → UUID-X  (SAME!)

Writer A: INSERT ... ON CONFLICT (spatial_id) DO NOTHING → inserted
Writer B: INSERT ... ON CONFLICT (spatial_id) DO NOTHING → skipped
```

Spectrum queries can **optionally** use spatial_id via joins, without schema changes:

```sql
-- Option A: Join via diaobject to get spatial_id
SELECT w.*, o.spatial_id, spatial_group(o.spatial_id) AS pos_group
FROM wantedspectra w
JOIN diaobject o ON w.root_diaobject_id = o.rootid
WHERE ...

-- Option B: Use spatial_group() for position-based filtering
-- Find wanted spectra for objects near a position
SELECT w.* FROM wantedspectra w
JOIN diaobject o ON w.root_diaobject_id = o.rootid
WHERE spatial_group(o.spatial_id) = spatial_group(generate_spatial_id(:ra, :dec, :mjd, 0, 0))
```

### Phase 1 Benefits

| Benefit | Impact |
|---------|--------|
| Multi-writer safe imports | Deterministic spatial_id prevents conflicts |
| Idempotent re-imports | `ON CONFLICT DO NOTHING` skips duplicates |
| Position-based grouping | `spatial_group(spatial_id)` available for queries |
| Backward compatible | All existing rootid-based queries unchanged |

---

## Phase 2 Migration (Proposed)

**Status:** Not yet implemented

Phase 2 will update spectrum tables to use `spatial_group` directly, eliminating the `root_diaobject` table dependency.

### Proposed Schema Changes

```sql
-- Step 1: Add spatial_group column to spectrum tables
ALTER TABLE wantedspectra ADD COLUMN spatial_group BIGINT;
ALTER TABLE spectruminfo ADD COLUMN spatial_group BIGINT;
ALTER TABLE plannedspectra ADD COLUMN spatial_group BIGINT;

-- Step 2: Backfill from existing root_diaobject_id
UPDATE wantedspectra w SET spatial_group = spatial_group(o.spatial_id)
FROM diaobject o WHERE w.root_diaobject_id = o.rootid;

UPDATE spectruminfo s SET spatial_group = spatial_group(o.spatial_id)
FROM diaobject o WHERE s.root_diaobject_id = o.rootid;

UPDATE plannedspectra p SET spatial_group = spatial_group(o.spatial_id)
FROM diaobject o WHERE p.root_diaobject_id = o.rootid;

-- Step 3: Create indexes
CREATE INDEX ON wantedspectra(spatial_group);
CREATE INDEX ON spectruminfo(spatial_group);
CREATE INDEX ON plannedspectra(spatial_group);

-- Step 4: Drop FK constraints to root_diaobject
ALTER TABLE wantedspectra DROP CONSTRAINT fk_wantedspectra_root_diaobject;
ALTER TABLE spectruminfo DROP CONSTRAINT fk_spectruminfo_root_diaobject;
ALTER TABLE plannedspectra DROP CONSTRAINT fk_plannedspectra_root_diaobject;

-- Step 5 (optional, after validation): Drop root_diaobject table
-- ALTER TABLE diaobject DROP CONSTRAINT fk_diaobject_rootid;
-- DROP TABLE root_diaobject;
```

### Populating spatial_group for New Spectrum Requests

```python
from spatial_id import generate_spatial_id, spatial_group_int

# When requesting a spectrum for object at (ra, dec):
target_sid = generate_spatial_id(ra, dec, mjd, procver, data_release)
target_group = spatial_group_int(target_sid)

# Insert with spatial_group (no root_diaobject lookup needed!)
cursor.execute("""
    INSERT INTO wantedspectra (spatial_group, user_id, requester, priority, wanttime)
    VALUES (%s, %s, %s, %s, NOW())
""", (target_group, user_id, requester, priority))
```

### Migrated Query Pattern (Phase 2)

```sql
-- OLD (requires root_diaobject join via diaobject)
SELECT t.root_diaobject_id, o.diaobjectid, s.*
FROM tmp_wanted_no_spec t
INNER JOIN diaobject o ON t.root_diaobject_id=o.rootid
INNER JOIN diasource s ON o.diaobjectid=s.diaobjectid

-- NEW (direct spatial_group join, no root_diaobject needed)
SELECT t.spatial_group, o.diaobjectid, s.*
FROM tmp_wanted_no_spec t
INNER JOIN diaobject o ON t.spatial_group = spatial_group(o.spatial_id)
INNER JOIN diasource s ON o.diaobjectid=s.diaobjectid
```

### Requesting a Spectrum (Phase 2 vs Current)

**Current (requires cone search):**
```python
# 1. Find diaobject by cone search
diaobject = SELECT * FROM diaobject WHERE q3c_radial_query(ra, dec, target_ra, target_dec, 1.0/3600)

# 2. Get or create root_diaobject
root_id = diaobject.rootid  # or INSERT INTO root_diaobject if new

# 3. Insert wanted spectrum
INSERT INTO wantedspectra (root_diaobject_id, ...) VALUES (root_id, ...)
```

**Phase 2 (no DB lookup needed):**
```python
from spatial_id import generate_spatial_id, spatial_group_int

# 1. Generate spatial_group directly from coordinates (no DB lookup!)
target_sid = generate_spatial_id(ra, dec, mjd, procver, dr)
target_group = spatial_group_int(target_sid)

# 2. Insert wanted spectrum using spatial_group
INSERT INTO wantedspectra (spatial_group, ...) VALUES (target_group, ...)

# 3. Query uses indexed spatial_group
SELECT * FROM diaobject WHERE spatial_group(spatial_id) = target_group
```

---

## Comparison: Three States

| Aspect | Current (rootid only) | Phase 1 (spatial_id added) | Phase 2 (spatial_id replaces) |
|--------|----------------------|---------------------------|------------------------------|
| **Object dedup** | Random UUID (conflicts) | `spatial_id` PK, `ON CONFLICT` | `spatial_id` PK |
| **Object lookup** | Cone search required | Cone search (unchanged) | Direct from coordinates |
| **Grouping** | `root_diaobject` table | `root_diaobject` (unchanged) | `spatial_group()` function |
| **Spectrum FK** | `root_diaobject_id` | `root_diaobject_id` (unchanged) | `spatial_group` column |
| **Multi-master** | UUID conflicts | Safe (`spatial_id` dedup) | Safe + simpler schema |
| **root_diaobject** | Required | Still in use | Removed |

---

## Cone Search vs Spatial Range Query

### Cone Search (q3c)

**What it does:** Find all objects within angular distance R of point (RA, Dec)

```sql
SELECT * FROM diaobject
WHERE q3c_radial_query(ra, dec, 150.123, 2.345, 1.0/3600);  -- 1 arcsec radius
```

**How it works:**
1. q3c pixelizes the sphere into hierarchical cells
2. Determines which cells overlap the search cone
3. Scans those cells from the q3c index
4. For each candidate, computes **great-circle distance**
5. Filters to objects where `d <= R`

**Cost:**
- Index lookup to find candidate cells
- Trigonometric calculation **per candidate row**
- Works on the actual RA/Dec columns

### Spatial Range Query (spatial_group)

**What it does:** Find all objects in the same HEALPix cell

```sql
SELECT * FROM diaobject
WHERE spatial_group(spatial_id) = 29268387604576;  -- exact cell match
```

**How it works:**
1. `spatial_group()` extracts top 48 bits of HEALPix from UUID
2. B-tree index lookup on precomputed BIGINT
3. **No trigonometry** - just integer equality

**Cost:**
- Single B-tree index seek
- Integer comparison only
- Function is precomputed in the functional index

### When Each is Appropriate

| Use Case | Query Type |
|----------|------------|
| "What's near this point?" | Cone search (q3c) |
| "What's at this exact position?" | Spatial range (`spatial_group`) |
| Cross-matching catalogs | Cone search |
| Grouping same object across observations | Spatial range |

For spectrum workflows, you're asking "give me all observations of **this object**" - that's an exact-position query, and `spatial_group` is purpose-built for it.

### Performance

```
Query: Find all diaobjects for a specific sky position

Cone search (1" radius):
  - Index: ~0.5ms (cell lookup)
  - Filter: ~0.1ms per candidate (trig)
  - Total: ~1-5ms depending on density

Spatial range (exact cell):
  - Index: ~0.1ms (B-tree seek)
  - Filter: none (exact match)
  - Total: ~0.1-0.3ms
```

For high-throughput spectrum pipelines, spatial_group is **10-50x faster** per lookup. But more importantly, with `spatial_group` you can compute the lookup key **without querying the database first** - eliminating a full round-trip.

---

## Index Efficiency

| Factor | UUID join (rootid) | BIGINT join (spatial_group) |
|--------|-------------------|----------------------------|
| **Index entry size** | 16 bytes | 8 bytes |
| **Comparison** | 16-byte memcmp | 64-bit integer compare |
| **Index locality** | Random (scattered) | Spatial (clustered by position) |

For 100M diaobjects, the spatial_group index shrinks from ~1.6GB to ~800MB.

---

## References

- [ADR_SPATIAL_ID_PHASE1_ADDITIVE.md](adr/ADR_SPATIAL_ID_PHASE1_ADDITIVE.md) - Phase 1 implementation details
- [ADR_SPATIAL_ID_PHASE2_FULL_REPLACEMENT.md](adr/ADR_SPATIAL_ID_PHASE2_FULL_REPLACEMENT.md) - Phase 2 proposal
- [db/2026-02-04_001_spatial_id_primary_key.sql](db/2026-02-04_001_spatial_id_primary_key.sql) - Phase 1 schema migration
- [db/2025-04-25-spectrumcycle.sql](db/2025-04-25-spectrumcycle.sql) - Current spectrum table schema
- [src/spectrum.py](src/spectrum.py) - Current spectrum query implementation
