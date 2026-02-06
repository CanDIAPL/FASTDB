# ADR: Spatial UUID Design for Multi-Master Replication

**Status:** Accepted
**Date:** 2026-01-12
**Author:** Carlo Costantini
**Updated:** 2026-01-29

## Context

FASTDB requires deterministic identifiers for astronomical objects that work correctly in a multi-master replication topology. The system must handle:

1. Multiple database writers ingesting the same objects independently
2. Objects observed multiple times at slightly different positions
3. Efficient spatial queries without full-table scans

The original approach used random UUIDs (`gen_random_uuid()`) combined with cone search matching. This had fundamental problems:

- **Non-deterministic**: Same object imported on different masters gets different UUIDs
- **Race conditions**: Concurrent imports can create duplicate root objects
- **Replication conflicts**: Random UUIDs from different masters collide during sync

## Decision

Add a `spatial_id` column to `diaobject` that encodes spatial position directly into a UUID using HEALPix indexing, creating **deterministic, position-encoded identifiers**. The existing `rootid` column (with its FK to `root_diaobject`) is retained — `spatial_id` is additive, not a replacement.

### UUID Bit Layout (128 bits)

```
High 64 bits: [Reserved:2][HEALPix:62]
Low 64 bits:  [MJD_ms:43][ProcVer:16][DataRelease:5]
```

| Field | Bits | Range | Purpose |
|-------|------|-------|---------|
| Reserved | 2 | 0 | Future use |
| HEALPix | 62 | NSIDE=2^29 | Sky position (~0.0004" resolution) |
| MJD_ms | 43 | 278 years | Observation time (millisecond precision) |
| ProcVer | 16 | 0-65535 | Processing version |
| DataRelease | 5 | 0-31 | Data release (0=realtime) |

### Primary Purpose: Deterministic Multi-Master IDs

**The fundamental goal is conflict-free replication.**

When multiple writers independently process the same astronomical alert:

```
Writer A: generate_spatial_id(ra=120.0, dec=45.0, mjd=60000.0, procver=0, dr=0)
          → UUID: 06780000-6660-7fe0-324a-9a7000000000

Writer B: generate_spatial_id(ra=120.0, dec=45.0, mjd=60000.0, procver=0, dr=0)
          → UUID: 06780000-6660-7fe0-324a-9a7000000000  (IDENTICAL)
```

Both writers produce the **exact same UUID** for the same input. When databases replicate:
- No conflicts (same primary key)
- No duplicates (idempotent via `ON CONFLICT DO NOTHING`)
- No coordination required between writers

This is the **primary architectural motivation** for spatial UUIDs.

### Secondary Benefit: Bit Masking for Fast Lookups

Because position is encoded in the high bits, we can extract spatial information using simple bit operations:

```sql
-- Extract coarse HEALPix cell (shift right 30 bits)
SELECT (high64 >> 30) AS matching_group FROM ...

-- Objects in same ~12" cell have same matching_group
CREATE INDEX ON diaobject(matching_group(spatial_id));
```

**This enables:**
- Index-based spatial filtering without trigonometric functions
- O(1) extraction of position information from any rootid
- Hierarchical spatial queries (coarser = fewer bits)

**This is a secondary benefit, not the primary purpose.**

## What Spatial UUIDs Are NOT

### NOT a Replacement for Cross-Matching

The `matching_group` and `matching_group_neighbors` functions are **convenience features** for quick proximity filtering. They are NOT designed to be:

- An exhaustive cross-matching system
- A replacement for Q3C cone searches
- A catalog association tool

**For proper cross-matching, use Q3C:**

```sql
-- CORRECT: Use Q3C for precise cross-matching
SELECT * FROM diaobject d1
JOIN external_catalog c ON q3c_join(d1.ra, d1.dec, c.ra, c.dec, 1.0/3600)

-- WRONG: Don't rely solely on matching_group for cross-matching
SELECT * FROM diaobject d1
JOIN external_catalog c ON matching_group(d1.spatial_id) = matching_group(c.spatial_id)
-- This misses objects near cell boundaries!
```

### NOT a Spatial Index

The matching_group index accelerates filtering but does not replace proper spatial indexing:

```sql
-- matching_group is a PRE-FILTER, not the final answer
SELECT * FROM diaobject
WHERE matching_group(spatial_id) = ANY(matching_group_neighbors(:target_mg))
  AND q3c_radial_query(ra, dec, :target_ra, :target_dec, 1.0/3600)
--    ^^^ Still need Q3C for precise distance calculation
```

### Intended Use Cases

| Use Case | Appropriate Tool |
|----------|------------------|
| Multi-master ID generation | `generate_spatial_id()` |
| Quick "nearby objects" filter | `matching_group_neighbors()` + Q3C verification |
| Precise cross-matching | Q3C cone search |
| Catalog association | Q3C + proper matching algorithms |
| Finding observations of same object | `spatial_group(spatial_id)` equality |

> **Note:** `spatial_id` is a separate column added alongside `rootid`. The existing `rootid` (with FK to `root_diaobject`) is retained for backward compatibility. Spatial functions operate on the `spatial_id` column.

## Design Principles

### 1. Determinism Over Flexibility

The UUID is fully determined by inputs. No randomness, no timestamps, no sequence numbers.

```python
# Always produces identical output for identical input
generate_spatial_id(ra, dec, mjd, procver, data_release)
```

### 2. Position-First Ordering

HEALPix occupies the high bits so that:
- UUIDs sort by sky position
- Range scans retrieve spatial regions
- B-tree indexes provide spatial locality

### 3. Hierarchical Extraction

Different precision levels extracted by shifting:

| Precision | Shift | Cell Size | Use Case |
|-----------|-------|-----------|----------|
| Storage | 0 | ~0.0004" | Exact position |
| Grouping | 16 | ~0.05" | Same-object identification |
| Matching | 30 | ~12.6" | Proximity queries |
| Coarse | 36 | ~100" | Wide-area filtering |

### 4. Self-Describing IDs

Any spatial_id can be decoded to recover:
- Approximate position (ra, dec)
- Observation time (mjd)
- Processing version
- Data release

No database lookup required.

## Implementation

### Python: Generation (requires healpy)

```python
from spatial_id import generate_spatial_id

sid = generate_spatial_id(
    ra=120.0,           # Right ascension (degrees)
    dec=45.0,           # Declination (degrees)
    mjd=60000.0,        # Modified Julian Date
    procver_compact=0,  # Processing version
    data_release=0      # Data release (0=realtime)
)
```

### SQL: Extraction (pure bit manipulation)

```sql
-- Extract matching group (no external dependencies)
SELECT matching_group(spatial_id) FROM diaobject;

-- Find nearby objects (convenience, not exhaustive)
SELECT * FROM diaobject
WHERE matching_group(spatial_id) = ANY(
    matching_group_neighbors(matching_group(:target_spatial_id))
);
```

### Architectural Boundary

```
┌─────────────────────────────────────────────┐
│ Application Layer (Python)                  │
│ - Generate spatial_id (requires healpy)     │
│ - HEALPix ang2pix() computation            │
│ - Complex spherical geometry                │
└─────────────────────────────────────────────┘
                    │
                    │ UUID bytes (opaque to SQL)
                    ↓
┌─────────────────────────────────────────────┐
│ Database Layer (SQL)                        │
│ - Extract bits from UUID (pure bit math)    │
│ - matching_group() = shift right 30 bits    │
│ - No external dependencies                  │
└─────────────────────────────────────────────┘
```

**Why this separation?**
- Python has healpy for HEALPix computation
- SQL needs PARALLEL SAFE for query performance
- Bit extraction is trivial; HEALPix computation is not

See [ADR_PYTHONvPGSQL.md](./ADR_PYTHONvPGSQL.md) for detailed trade-off analysis.

## Consequences

### Positive

1. **Conflict-free multi-master**: Same object → same UUID everywhere
2. **Idempotent imports**: Duplicate data safely ignored
3. **No coordination**: Writers operate independently
4. **Self-describing**: Position recoverable from UUID
5. **Index-friendly**: Bit extraction enables fast filtering

### Negative

1. **healpy dependency**: Generation requires scientific Python stack
2. **Frozen format**: Bit layout cannot change without migration
3. **Learning curve**: Teams must understand when to use Q3C vs matching_group

### Neutral

1. **Query patterns expand**: Use `spatial_group(spatial_id)` for spatial queries alongside existing rootid-based JOINs
2. **Matching complexity moves**: From insert-time to query-time for spatial lookups

## Testing Strategy

### Determinism Tests

```python
def test_multi_master_determinism():
    """Same inputs on different 'masters' produce identical UUIDs."""
    args = (120.0, 45.0, 60000.0, 0, 0)

    # Simulate multiple independent writers
    uuid1 = generate_spatial_id(*args)
    uuid2 = generate_spatial_id(*args)
    uuid3 = generate_spatial_id(*args)

    assert uuid1 == uuid2 == uuid3
```

### Parity Tests

SQL extraction must match Python:

```python
def test_sql_python_parity():
    sid = generate_spatial_id(120.0, 45.0, 60000.0, 0, 0)

    py_mg = matching_group_int(sid)
    sql_mg = db.query("SELECT matching_group(%s)", [sid])

    assert py_mg == sql_mg
```

## References

- [spatial_id.py](./src/spatial_id.py) - Python implementation
- [2026-01-20_001_matching_group.sql](./db/2026-01-20_001_matching_group.sql) - SQL functions
- [ADR_PYTHONvPGSQL.md](./ADR_PYTHONvPGSQL.md) - Implementation language decision
- [test_spatial_id.py](./tests/test_spatial_id.py) - Python tests
- [test_matching_group.sql](./db/test_matching_group.sql) - SQL tests
- [test_matching_group_parity.py](./tests/test_matching_group_parity.py) - Parity tests
