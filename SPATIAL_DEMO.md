# Spatial UUID Overview

## What Was Done

Created a deterministic UUID system (`spatial_id`) for multi-master replication without conflicts. The `spatial_id` is added as a separate column on `diaobject` alongside the existing `rootid`.

### UUID Bit Layout (128 bits, big-endian)
```
high64: [Reserved:2][HEALPix:62]
low64:  [MJD_ms:43][ProcVer:16][DataRelease:5]
```

**Field ordering enables hierarchical filtering:** position -> time -> procver -> data_release

### Key Properties
- **HEALPix** at NSIDE=2^29 provides ~0.0004" spatial precision
- **Spatial grouping** uses top 48 bits of HEALPix (~0.05" resolution)
- **Deterministic**: Same (ra, dec, mjd, procver, dr) -> same UUID on any master
- **Position-only grouping**: Objects at same position share group regardless of time/procver/dr

### Files Created/Modified
- `src/spatial_id.py` - Core generation and extraction functions
- `tests/test_spatial_id.py` - 64 comprehensive tests
- `db/2026-01-07_001_procver_compact_id.sql` - SQL `spatial_group()` function and index
- `scripts/demo_spatial_id.py` - Practical workflow demonstration

## What the Demo Provides

The demo (`scripts/demo_spatial_id.py`) shows:

1. **Coordinate -> UUID workflow**: How RA/Dec coordinates become a spatial_id
2. **Same-position grouping**: Multiple observations at one position share the same spatial_group
3. **Query comparison**: Old rootid-based queries vs new spatial_id queries
4. **Multi-precision filtering**: Adjustable spatial resolution via bit shifting
5. **Hierarchical filtering**: Position first, then time, then procver/dr

## Intent

Add position-encoded UUIDs as a `spatial_id` column so that:
- **Multi-master safe** - identical inputs produce identical UUIDs
- **Efficient spatial queries** - indexed `spatial_group(spatial_id)` function
- **Self-describing** - position, time, version recoverable from UUID
- **Backward compatible** - existing `rootid` and `root_diaobject` retained
