# Scripts

## Deployment

### `helm-deploy.sh`

End-to-end Helm deployment of FASTDB to Kubernetes. Builds the
`install/` directory, runs `helm upgrade --install`, copies code to the
shared PVC via the shell pod, and restarts dependent pods.

```bash
./scripts/helm-deploy.sh [NAMESPACE] [VALUES_FILE] [OPTIONS]

# Local Kind cluster from scratch
./scripts/helm-deploy.sh local ./helm/fastdb/values-local.yaml \
  --create-cluster admin/local/kind-config.yaml

# SLAC dev namespace, skip rebuild
./scripts/helm-deploy.sh <namespace> ./helm/fastdb/values-<namespace>.yaml \
  --skip-build --context desc-fastdb
```

Key flags: `--skip-build`, `--skip-helm`, `--create-cluster`,
`--load-images`, `--registry-password`, `--external-url`.

### `slac-fastdb-login.sh`

Configures kubectl credentials for the SLAC `desc-fastdb` vcluster.
Requires `SLAC_USER` and `SLAC_TOKEN` environment variables.

```bash
export SLAC_USER=myuser
export SLAC_TOKEN=<token from https://k8s.slac.stanford.edu/desc-fastdb>
./scripts/slac-fastdb-login.sh
```

## Database Seeding

### `seed_realtime_procver.sql`

Idempotently creates the `realtime` processing version chain needed
before any diaobjects can be imported. Seeds `base_processing_version`,
`processing_version`, `base_procver_of_procver`, and
`procver_compact_id`. Safe to run multiple times.

```bash
# From shell pod
PGPASSWORD=$(cat /secrets/pgpasswd) psql -h postgres -U postgres -d fastdb \
  -f /fastdb/scripts/seed_realtime_procver.sql
```

### `seed_mongodb_alerts.py`

Seeds MongoDB with synthetic alert documents (diaObject + diaSource +
prvDiaSources + prvDiaForcedSources) for testing a single
`source_importer` run. Each alert document matches the format the
importer expects from Kafka.

```bash
# From shell pod (uses default MongoDB connection inside the cluster)
PYTHONPATH=/fastdb python /fastdb/scripts/seed_mongodb_alerts.py \
  --collection test_alerts --num-objects 50 --clear

# Then import into PostgreSQL
PYTHONPATH=/fastdb python -m services.source_importer -p realtime -o realtime \
  -c test_alerts
```

Flags: `-H` host, `-d` dbname, `-c` collection, `-u` username, `-p`
password, `-n` num-objects, `--base-object-id`, `--clear`.

### `seed_nearby_transient.py`

Seeds MongoDB with 5 observations of a simulated transient at
RA=120, Dec=45. Each observation is at a slightly different position
(within 0.5") with a brightening/fading light curve over 6 days.
Includes previous sources and forced sources (pre-transient
non-detections). Useful for testing rootid matching of nearby
observations and light curve assembly.

```bash
PYTHONPATH=/fastdb python /fastdb/scripts/seed_nearby_transient.py

# Then import
PYTHONPATH=/fastdb python -m services.source_importer -p realtime -o realtime \
  -c test_alerts
```

### `seed_boundary_objects.py`

Seeds MongoDB with pairs of objects that straddle HEALPix pixel
boundaries at `NSIDE_MATCHING` (2^14, ~12.6" cells). Creates 4
test scenarios:

1. **East-West boundary** -- pairs across the RA edge of a pixel
2. **North-South boundary** -- pairs across the Dec edge
3. **Corner crossing** -- pairs at a diagonal pixel junction
4. **Base pixel boundary** -- pairs across the boundary between HEALPix
   base pixels (hardest case)

Each pair is ~0.8" apart but lands in different HEALPix cells. After
importing, run `demo_boundary_matching.sql` to verify that
`matching_group_neighbors()` finds all pairs.

```bash
PYTHONPATH=/fastdb python /fastdb/scripts/seed_boundary_objects.py
```

## Demonstrations

### `multiwriter_demo/` (Multi-Writer Source Importer)

Demonstrates that multiple `source_importer` instances can run
concurrently without conflicts. The `spatial_id` primary key is
deterministic -- same (RA, Dec, MJD, procver, data_release) always
produces the same UUID -- so `ON CONFLICT (spatial_id) DO NOTHING`
silently deduplicates across concurrent writers.

**Prerequisites:** The `spatial_id` PK migration must be applied:

```bash
PGPASSWORD=$(cat /secrets/pgpasswd) psql -h postgres -U postgres -d fastdb \
  -f /fastdb/db/2026-02-04_001_spatial_id_primary_key.sql
```

**Quick start (single command):**

```bash
PYTHONPATH=/fastdb python /fastdb/scripts/multiwriter_demo/run_demo.py
```

This cleans previous data, generates 100 objects with 30% overlap across
3 partitions, launches 3 concurrent importers, and runs verification.
A successful run prints `SUCCESS: Multi-writer import completed without
conflicts!`

**Step-by-step:**

```bash
# 1. Generate test data into MongoDB
PYTHONPATH=/fastdb python /fastdb/scripts/multiwriter_demo/generate_test_data.py \
  --collection multiwriter_test --num-objects 100 --partitions 3 --overlap 0.3

# 2. Run concurrent importers (reuse existing data)
PYTHONPATH=/fastdb python /fastdb/scripts/multiwriter_demo/run_demo.py \
  --skip-clean --skip-generate -n 100 -p 3 -o 0.3

# 3. Verify results independently
PYTHONPATH=/fastdb python /fastdb/scripts/multiwriter_demo/verify_multiwriter.py \
  --expected-objects 100
```

**Larger configurations:**

```bash
# 1000 objects, 5 detections each, 6 importers, 50% overlap
PYTHONPATH=/fastdb python /fastdb/scripts/multiwriter_demo/run_demo.py \
  -n 1000 -d 5 -p 6 -o 0.5
```

**Files:**

| File | Purpose |
|------|---------|
| `run_demo.py` | Main entry point. Orchestrates clean, generate, import, verify. |
| `generate_test_data.py` | Creates synthetic alerts in MongoDB with configurable overlap between time partitions. |
| `verify_multiwriter.py` | Post-import checks: no duplicate spatial_ids, deterministic regeneration, spatial grouping, object counts, root_diaobject integrity. |

**`run_demo.py` flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `-n / --num-objects` | 100 | Number of unique sky objects |
| `-d / --detections` | 3 | Detections (MJDs) per object |
| `-p / --partitions` | 3 | Number of concurrent importer threads |
| `-o / --overlap` | 0.3 | Fraction of objects shared across all partitions |
| `--skip-clean` | off | Skip cleaning previous data |
| `--skip-generate` | off | Skip data generation (reuse existing MongoDB data) |

### `demo_spatial_id.py` (Spatial ID Encoding)

Pure-Python demonstration of the `spatial_id` UUID encoding. Does not
require a database. Shows:

1. Coordinate-to-UUID conversion and recovered precision
2. Multiple observations at the same position share a `spatial_group`
3. Side-by-side SQL: old rootid queries vs new spatial_id queries
4. Multi-precision filtering via bit-shifting spatial_group
5. Hierarchical position-then-time query patterns

```bash
python scripts/demo_spatial_id.py
```

### `demo_boundary_matching.sql`

SQL script that demonstrates HEALPix boundary matching. Run after
`seed_boundary_objects.py` and importing with `source_importer`. Shows
that pairs <1" apart can land in different HEALPix cells, and that
`matching_group_neighbors()` reliably finds all of them. Includes a
performance comparison of `matching_group` pre-filter + Q3C vs Q3C
alone.

```bash
# On the postgres pod
psql -U postgres -d fastdb -f /fastdb/scripts/demo_boundary_matching.sql
```

### `run_boundary_simulation.sh`

End-to-end wrapper that runs the full boundary matching demo on a
Kubernetes cluster: copies `seed_boundary_objects.py` to the shell pod,
seeds MongoDB, imports via `source_importer`, then runs
`demo_boundary_matching.sql` on the postgres pod.

```bash
./scripts/run_boundary_simulation.sh [NAMESPACE]
# default namespace: fastdb-local
```

## Benchmarks

### `benchmark_spatial_vs_cone.py`

Benchmarks comparing spatial_id queries to traditional cone searches.
Runs 6 benchmarks:

1. **Pure computation** -- trig (sin/cos/acos per row) vs integer equality
2. **UUID vs BIGINT comparison** -- raw equality-check cost
3. **Spatial ID generation speed** -- HEALPix + bit-packing throughput
4. **PostgreSQL query performance** (with `--with-db`) -- q3c cone search
   vs `spatial_group()` index vs direct `spatial_id` lookup
5. **Index size comparison** -- projected B-tree sizes at 1M to 1B rows
6. **Lookup-free advantage** -- DB round-trip elimination for group resolution

```bash
# Pure computation benchmarks (no database needed)
python scripts/benchmark_spatial_vs_cone.py

# Quick mode (fewer iterations)
python scripts/benchmark_spatial_vs_cone.py --quick

# Include PostgreSQL benchmarks (requires running FASTDB with data)
PYTHONPATH=/fastdb python /fastdb/scripts/benchmark_spatial_vs_cone.py --with-db
```
