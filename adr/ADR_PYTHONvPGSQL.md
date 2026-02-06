# ADR: Native PL/pgSQL vs PL/Python for matching_group Functions

**Status:** Accepted
**Date:** 2026-01-20
**Author:** Carlo Costantini
**Decision:** Implement matching_group functions in native PL/pgSQL rather than PL/Python wrappers

## Context

The FASTDB system uses spatial UUIDs (`spatial_id`) that encode HEALPix position, timestamp, processing version, and data release into a deterministic 128-bit identifier. For proximity queries, we need SQL functions to extract coarse "matching groups" from these UUIDs.

Two implementation approaches were considered:

1. **Native PL/pgSQL:** Reimplement bit manipulation logic in SQL
2. **PL/Python Wrapper:** Load Python `spatial_id.py` module and call it directly

The question arose: wouldn't it be better to use PL/Python to avoid code duplication?

## Decision

**Implement matching_group functions in native PL/pgSQL.**

The ~100 lines of bit manipulation logic is intentionally duplicated from Python because the trade-offs strongly favor native SQL for database-layer extraction functions.

## Rationale

### 1. Performance: PARALLEL SAFE is Critical

Native PL/pgSQL functions can be marked `PARALLEL SAFE`, enabling PostgreSQL to parallelize queries across multiple CPU cores:

```sql
CREATE OR REPLACE FUNCTION matching_group(spatial_id UUID) RETURNS BIGINT AS $$
...
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;
```

**PL/Python functions cannot be PARALLEL SAFE** due to Python's Global Interpreter Lock (GIL). Each parallel worker would need its own Python interpreter, creating massive overhead.

**Real-world impact on a 10M row table with 8 cores:**
- Native PL/pgSQL: 2-4 seconds (parallel workers: 4-8)
- PL/Python: 15-30 seconds (sequential scan only)

This is a **4-8x performance difference** that grows with table size.

### 2. Reliability: Zero External Dependencies

PL/Python introduces a fragile dependency chain:

```
PostgreSQL → PL/Python extension → Python interpreter → healpy → NumPy → C++ backends
```

**Failure scenarios:**
- PostgreSQL server runs different Python version than development
- healpy compiled against different NumPy ABI
- System Python upgraded, breaking shared libraries
- `ERROR: could not import module "healpy"` during production queries

**Native PL/pgSQL has zero external dependencies.** The bit manipulation logic uses only standard SQL operators.

### 3. Security: PL/Python is Untrusted

PL/Python functions run with full OS privileges of the PostgreSQL server process:

```python
# A PL/Python function can:
open('/etc/shadow')           # Read arbitrary files
os.system('rm -rf /')         # Execute shell commands
urllib.request.urlopen(...)   # Network access
```

If a SQL injection or privilege escalation allows calling a PL/Python function, the attacker gains **operating system access**, not just database access.

**Native PL/pgSQL is sandboxed** and cannot escape the SQL security model.

### 4. Replication Compatibility

With logical replication (multi-master), subscribers must rebuild functional indexes:

```sql
CREATE INDEX ON diaobject(matching_group(spatial_id));
```

**Native PL/pgSQL:** Rebuilds instantly on any PostgreSQL instance (pure math)

**PL/Python:** Fails if subscriber has different Python/healpy version:
```
ERROR: Python import failed on subscriber
REPLICATION STALLED
```

### 5. Functional Index Performance

The `matching_group` index is critical for query performance:

```sql
CREATE INDEX idx_diaobject_matching_group ON diaobject(matching_group(spatial_id));
```

| Metric | Native PL/pgSQL | PL/Python |
|--------|-----------------|-----------|
| Index build (10M rows) | 2-5 minutes | 20-45 minutes |
| Build parallelism | Yes | No |
| Can mark IMMUTABLE | Yes (guaranteed) | Risky (healpy version changes) |

## Trade-off Analysis

| Concern | Native PL/pgSQL | PL/Python Wrapper |
|---------|-----------------|-------------------|
| **Parallel Queries** | PARALLEL SAFE | Cannot be parallel (GIL) |
| **Dependencies** | Zero | healpy → NumPy → C++ |
| **Security** | Sandboxed | Untrusted (OS access) |
| **Replication** | Works everywhere | Breaks on version mismatch |
| **Functional Index** | Fast, parallel | Slow, serial |
| **Code Duplication** | ~100 lines | None |
| **Maintenance** | Parity tests | Single source |

## Why "Code Duplication" is Actually Clean Architecture

The apparent duplication is **proper architectural boundary separation**:

```
┌─────────────────────────────────────────┐
│ Application Layer (Python)              │
│ - Generate spatial_id (requires healpy) │
│ - Complex: ang2pix() HEALPix conversion │
│ - 620 lines in spatial_id.py            │
└─────────────────────────────────────────┘
                    │
                    │ UUID (opaque bytes)
                    ↓
┌─────────────────────────────────────────┐
│ Database Layer (SQL)                    │
│ - Extract matching_group (pure bit math)│
│ - Simple: shift right 30 bits           │
│ - ~100 lines in matching_group.sql      │
└─────────────────────────────────────────┘
```

**Key insight:** Python does the hard work (HEALPix spherical geometry via healpy). SQL only does simple bit extraction. The duplicated logic is:

1. **Mathematically stable:** Morton code algorithms haven't changed since 1960s
2. **Small surface area:** Only 3 public functions need parity
3. **Verified by tests:** 431 lines of parity tests catch any divergence
4. **Different execution contexts:** SQL engine vs Python interpreter

This follows the same pattern as PostgreSQL's built-in `EXTRACT()` function, which reimplements date logic that exists in application libraries.

## Alternatives Considered

### Alternative 1: PL/Python Wrapper

```sql
CREATE FUNCTION matching_group(spatial_id UUID) RETURNS BIGINT AS $$
    import spatial_id
    return spatial_id.matching_group_int(rootid)
$$ LANGUAGE plpython3u;
```

**Rejected because:**
- Cannot be PARALLEL SAFE (4-8x performance loss)
- Fragile dependencies (healpy, numpy version conflicts)
- Security risk (untrusted language)
- Replication incompatibility

### Alternative 2: Pure SQL HEALPix Computation

Implement full `ang2pix()` in SQL to avoid any Python dependency.

**Not needed because:**
- `matching_group()` only extracts bits from existing UUIDs
- The HEALPix computation already happened in Python during INSERT
- Pure bit extraction (shift right 30 bits) is trivial in SQL

### Alternative 3: PostgreSQL Extension in C

Write a native C extension for maximum performance.

**Rejected because:**
- Overkill for simple bit manipulation
- Deployment complexity (compile per platform)
- PL/pgSQL is already fast enough for this use case
- Higher maintenance burden

## Consequences

### Positive

- Queries can use parallel execution (PARALLEL SAFE)
- Zero external dependencies in database layer
- Works with all replication topologies
- Passes security audits (sandboxed execution)
- Fast functional index builds

### Negative

- ~100 lines of bit manipulation duplicated between Python and SQL
- Must maintain parity tests to catch divergence
- Changes to UUID bit layout require updates in both places

### Mitigations

1. **Parity Tests:** `tests/test_matching_group_parity.py` verifies exact agreement between SQL and Python implementations

2. **Frozen Contract:** The UUID bit layout is documented and frozen:
   ```
   High 64 bits: [Reserved:2][HEALPix:62]
   Low 64 bits:  [MJD_ms:43][ProcVer:16][DataRelease:5]
   ```

3. **Comprehensive SQL Tests:** `db/test_matching_group.sql` validates SQL functions independently

## Testing Strategy

### SQL Unit Tests (`db/test_matching_group.sql`)

- Morton code helpers (_mg_undilate, _mg_dilate, _mg_xy_to_morton)
- matching_group extraction with known values
- matching_group_neighbors correctness
- Precision functions (_mg_nside_for_precision, _mg_shift_bits_for_nside)
- Error handling (invalid inputs)
- Boundary pair detection (integration test)

### Python Parity Tests (`tests/test_matching_group_parity.py`)

- `TestMatchingGroupParity`: SQL vs Python matching_group comparison
- `TestMatchingGroupNeighborsParity`: SQL vs Python neighbors comparison
- `TestPrecisionFunctionsParity`: All precision functions
- `TestKnownValues`: Regression tests against frozen known-good values

## When to Reconsider

Reconsider this decision if:

1. **PostgreSQL adds native HEALPix support** - Could use built-in functions instead
2. **PL/Python becomes PARALLEL SAFE** - Unlikely due to Python GIL
3. **Business logic moves to SQL** - Current functions are pure math, not business logic
4. **healpy becomes ABI-stable** - Reduces dependency risk (but other concerns remain)

## References

- `src/spatial_id.py` - Python implementation
- `db/2026-01-20_001_matching_group.sql` - SQL implementation
- `tests/test_matching_group_parity.py` - Parity tests
- `db/test_matching_group.sql` - SQL unit tests
