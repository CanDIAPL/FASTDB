# ADR: Phase 2 — Full spatial_id Replacement of rootid

**Status:** Proposed (not yet implemented)
**Date:** 2026-01-12
**Author:** Carlo Costantini
**Supersedes:** N/A
**Depends on:** [ADR_SPATIAL_ID_PHASE1_ADDITIVE.md](./ADR_SPATIAL_ID_PHASE1_ADDITIVE.md) (implemented)

> **Note:** This ADR describes a future state. Phase 1 (additive spatial_id column) has been
> implemented and is documented separately. This Phase 2 proposes removing the legacy rootid
> infrastructure entirely once Phase 1 has been validated in production.

## Context

After Phase 1, the `source_importer.py` module has two parallel identification systems:

1. **rootid** (legacy): Cone-search matching + random UUID + `root_diaobject` table
2. **spatial_id** (new): Deterministic UUID from (ra, dec, mjd, procver, data_release)

Phase 1 kept both to allow a smooth migration. This Phase 2 proposes removing the legacy
system entirely once we're confident spatial_id works correctly.

### Current State (Post-Phase 1)

The `source_importer.py` module assigns `rootid` values using:

1. **Cone search matching** (`q3c_radial_query`) to link new objects to existing root objects within 1 arcsecond
2. **Random UUID generation** (`gen_random_uuid()`) for objects that don't match existing ones
3. **Insertion into `root_diaobject` table** to maintain referential integrity

This approach has several limitations:

- **Multi-master conflicts**: Random UUIDs generated on different masters will differ for the same object
- **Cone search overhead**: Trigonometric calculations per candidate row
- **Lookup table dependency**: Requires `root_diaobject` table for grouping
- **Non-deterministic**: Same input data produces different rootids on different runs
- **Race conditions**: Concurrent imports can create duplicate root objects with different UUIDs

The `spatial_id` module provides deterministic, position-encoded UUIDs that solve these problems.

## Decision

1. **Direct spatial_id assignment**: Each diaobject gets `rootid = generate_spatial_id(ra, dec, mjd, procver, data_release)`
2. **No matching at insert time**: Grouping happens at query time via `spatial_group(rootid)`
3. **Remove the `root_diaobject` table**: It becomes redundant with deterministic spatial_id
4. **Update spectrum tables**: Remove FK constraints to `root_diaobject`

### Design Principles

1. **Deterministic**: Same (ra, dec, mjd, procver, data_release) always produces same rootid
2. **Simple defaults**: `procver=0` and `data_release=0` unless explicitly provided
3. **No insert-time matching**: Each object gets its own spatial_id; grouping is query-time
4. **Eliminate lookup table**: `root_diaobject` table removed entirely
5. **Idempotent imports**: Same data imported twice produces identical rootids → `ON CONFLICT DO NOTHING`

### Key Insight: Why No Matching?

Each diaobject has unique (diaobjectid, base_procver_id). The spatial_id encodes (ra, dec, mjd, procver, data_release).

- **Same observation imported twice** → same spatial_id → idempotent (handled by `ON CONFLICT`)
- **Different observations of same physical object** → different mjd → different spatial_id
- **Grouping at query time** → use `spatial_group(rootid)` which extracts position-only bits

The `spatial_group()` function ignores mjd/procver/data_release bits, so objects at the same position (regardless of observation time) share the same group.

## Detailed Design

### 1. Direct Assignment (No Matching)

```python
from spatial_id import generate_spatial_id

def assign_rootid(ra, dec, mjd, procver=0, data_release=0):
    """Generate deterministic rootid directly from coordinates."""
    return generate_spatial_id(ra, dec, mjd, procver, data_release)
```

This is simpler than the original two-level matching approach because:
- No memory cache of existing objects needed
- No neighbor cell lookups needed
- No insert-time matching logic
- Grouping happens at query time via `spatial_group(rootid)`

### 2. Handling Cell Boundaries (Query Time)

The cell boundary problem (objects 0.5" apart in different cells) is handled at **query time**, not insert time:

```sql
-- Find all observations of objects near position (ra, dec)
SELECT * FROM diaobject
WHERE spatial_group(rootid) IN (
    -- Target cell + 8 neighbors for robust matching
    SELECT unnest(get_neighbor_groups(:ra, :dec))
);
```

This moves complexity to queries that need approximate matching, while keeping inserts simple.

### 3. Schema Changes

#### Remove root_diaobject Table

```sql
-- Migration: Remove root_diaobject table and FK constraints

-- Step 1: Drop FK from diaobject
ALTER TABLE diaobject DROP CONSTRAINT IF EXISTS fk_diaobject_rootid;

-- Step 2: Drop FK from spectrum tables
ALTER TABLE spectruminfo DROP CONSTRAINT IF EXISTS fk_spectruminfo_root_diaobject;
ALTER TABLE wantedspectra DROP CONSTRAINT IF EXISTS fk_wantedspectra_root_diaobject;
ALTER TABLE plannedspectra DROP CONSTRAINT IF EXISTS fk_plannedspectra_root_diaobject;

-- Step 3: Drop the table
DROP TABLE IF EXISTS root_diaobject;

-- Step 4: Add index for spatial_group queries (if not exists)
CREATE INDEX IF NOT EXISTS idx_diaobject_spatial_group
ON diaobject(spatial_group(rootid));
```

#### Spectrum Tables Update

The spectrum tables (`spectruminfo`, `wantedspectra`, `plannedspectra`) currently have:
```sql
root_diaobject_id UUID REFERENCES root_diaobject(id)
```

After migration, they will have:
```sql
root_diaobject_id UUID  -- No FK constraint, stores spatial_id directly
```

The `root_diaobject_id` column name is kept for backward compatibility, but it now stores spatial_id values directly rather than referencing a lookup table.

### 4. Function Signature Changes

**Current `__init__`:**
```python
def __init__(self, base_processing_version, object_base_processing_version,
             object_match_radius=1.):
```

**New `__init__`:**
```python
def __init__(self, base_processing_version, object_base_processing_version,
             procver=0, data_release=0):
```

New parameters:
- `procver`: Integer processing version for spatial_id encoding (default: 0)
- `data_release`: Integer data release identifier (default: 0, meaning realtime)

Note: `object_match_radius` is removed - no longer needed since we don't do insert-time matching.

### 5. Import Workflow (Simplified)

The new workflow is much simpler - just compute spatial_id and insert:

```python
def import_objects_from_collection(self, collection, t0=None, t1=None,
                                    batchsize=10000, conn=None, commit=True):
    """Import diaobject records using spatial_id."""

    with db.DB(conn) as pqconn:
        # Step 1: Read objects from MongoDB into temp table (existing method)
        self.read_mongo_objects(pqconn, collection, t0=t0, t1=t1, batchsize=batchsize)

        cursor = pqconn.cursor()

        # Step 2: Filter to new objects only
        cursor.execute("DROP TABLE IF EXISTS temp_new_diaobject")
        cursor.execute("""
            CREATE TEMP TABLE temp_new_diaobject AS
            SELECT tdi.* FROM temp_diaobject_import tdi
            LEFT JOIN diaobject o ON
                o.diaobjectid = tdi.diaobjectid AND o.base_procver_id = tdi.base_procver_id
            WHERE o.diaobjectid IS NULL
        """)

        # Step 3: Assign rootid using spatial_id (in Python)
        cursor.execute("SELECT diaobjectid, ra, dec, validitystartmjdtai FROM temp_new_diaobject")
        rows = cursor.fetchall()

        for diaobjectid, ra, dec, mjd in rows:
            rootid = generate_spatial_id(ra, dec, mjd, self.procver, self.data_release)
            cursor.execute(
                "UPDATE temp_new_diaobject SET rootid = %s WHERE diaobjectid = %s",
                (str(rootid), diaobjectid)
            )

        # Step 4: Insert into root_diaobject (for backward compat during migration)
        cursor.execute("""
            INSERT INTO root_diaobject (id)
            SELECT DISTINCT rootid FROM temp_new_diaobject
            ON CONFLICT DO NOTHING
        """)

        # Step 5: Insert new objects
        cursor.execute("INSERT INTO diaobject SELECT * FROM temp_new_diaobject")
        nobjs = cursor.rowcount

        if commit:
            pqconn.commit()

        return nobjs
```

**Key simplifications:**
- No cone search matching
- No memory cache of existing objects
- Each object gets its own unique rootid
- Grouping happens at query time via `spatial_group(rootid)`

### 6. Handling Concurrent Imports

With deterministic spatial_id, concurrent imports are naturally safe:

```
Process A: generate_spatial_id(ra, dec, mjd, 0, 0) → SPATIAL-1
Process B: generate_spatial_id(ra, dec, mjd, 0, 0) → SPATIAL-1  (SAME!)
```

Both processes generate identical rootids for the same object. Handle duplicates with:

```python
cursor.execute("""
    INSERT INTO diaobject (...)
    VALUES (...)
    ON CONFLICT (diaobjectid, base_procver_id) DO NOTHING
""")
```

No race condition because:
1. Same object → same spatial_id (deterministic)
2. Duplicate insert → silently skipped (ON CONFLICT DO NOTHING)
3. No external state to synchronize

### 8. Migration Strategy

#### Phase 1: Update source_importer
- Add spatial_id generation
- Keep inserting to root_diaobject (for backward compat)
- New imports get spatial_id-based rootids

#### Phase 2: Remove root_diaobject dependency
- Update spectrum tables to remove FK constraints
- Update spectrum queries to use spatial_group instead of join
- Drop root_diaobject table

#### Phase 3: Backfill (Optional)
- Update existing random rootids to spatial_ids
- Can be done incrementally by sky region
- Not required for new functionality to work

## Consequences

### Positive

1. **Deterministic rootids**: Same object imported on different masters gets same rootid
2. **Multi-master safe**: No UUID conflicts during replication
3. **Simpler insert path**: No cone search, no matching cache, just compute and insert
4. **No race conditions**: Concurrent imports produce identical rootids for same data
5. **Simpler schema**: No root_diaobject lookup table (after migration)
6. **Position-encoded**: rootid encodes (ra, dec, mjd, procver, dr) - self-describing
7. **No lookup required**: Can compute spatial_group from coordinates without DB query

### Negative

1. **Python dependency**: Spatial ID generation requires healpy library
2. **Query complexity for approximate matching**: Cell boundary handling moves to query time

### Neutral

1. **Spectrum tables updated**: FK removed but column name kept for compatibility
2. **Index still needed**: `spatial_group(rootid)` index required for efficient queries

## Performance Comparison

| Operation | Old (cone search) | New (direct spatial_id) |
|-----------|-------------------|-------------------------|
| **Insert path** | Cone search + UUID | Direct compute |
| **UUID generation** | O(1) random | O(1) deterministic |
| **Memory requirement** | None | None |
| **Multi-master** | Conflict risk | Conflict-free |
| **Concurrent imports** | Race condition risk | Safe (idempotent) |
| **Grouping** | JOIN on rootid | spatial_group() function |

## Implementation Checklist

- [ ] Add `procver` and `data_release` parameters to `__init__`
- [ ] Remove `object_match_radius` parameter
- [ ] Replace cone search matching with direct spatial_id assignment
- [ ] Rewrite `import_objects_from_collection()` with simplified workflow
- [ ] Create migration to drop root_diaobject FK constraints
- [ ] Create migration to drop root_diaobject table
- [ ] Update spectrum table queries to use spatial_group
- [ ] Add unit tests for spatial_id assignment
- [ ] Add integration tests for concurrent imports
- [ ] Verify index usage with EXPLAIN ANALYZE

## Test Plan

### Unit Tests
1. Verify spatial_id generation produces consistent results
2. Verify same (ra, dec, mjd, procver, dr) → same rootid
3. Verify different mjd → different rootid but same spatial_group
4. Verify concurrent generation produces identical rootids

### Integration Tests
1. Import same object twice → same rootid, `ON CONFLICT DO NOTHING` works
2. Import objects on different "masters" → identical rootids
3. Concurrent import stress test → no conflicts
4. Performance test: Compare import time with old vs new method

### Migration Tests
1. FK constraints removed successfully
2. root_diaobject table dropped
3. Existing queries still work
4. Spectrum queries return correct results using spatial_group

## Alternatives Considered

### Alternative 1: Pure SQL Spatial ID Generation

Create a SQL function `generate_spatial_id_sql(ra, dec, mjd, procver, dr)` to avoid Python round-trip.

**Rejected because:**
- HEALPix computation requires healpy library
- PostgreSQL doesn't have native HEALPix support
- Would need PL/Python extension, adding deployment complexity

### Alternative 2: Keep root_diaobject Table

Maintain the lookup table for backward compatibility.

**Rejected because:**
- Adds unnecessary complexity
- spatial_id makes it redundant
- Deterministic IDs eliminate the race conditions it was designed to handle
- Removing it simplifies schema and reduces insert overhead

### Alternative 3: Insert-Time Matching (Two-Level HEALPix)

Match objects at insert time using coarse HEALPix cells + neighbor checking.

**Rejected because:**
- Adds complexity to insert path
- Requires memory cache of existing objects
- Same result achievable at query time via spatial_group()
- The key insight: each diaobject has unique (ra, dec, mjd, procver) → unique rootid

## References

- [ADR_SPATIAL_UUID.md](./ADR_SPATIAL_UUID.md) - Original spatial_id design
- [EXAMPLE_PHASE2_SPATIAL_SPECTRA.md](./SPATIAL_SPECTRA.md) - Spectrum query integration
- [spatial_id.py](./src/spatial_id.py) - Implementation
- [benchmark_spatial_vs_cone.py](./scripts/benchmark_spatial_vs_cone.py) - Performance benchmarks
