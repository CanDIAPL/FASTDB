-- seed_realtime_procver.sql
-- Idempotently creates the "realtime" processing version chain needed
-- before any diaobjects can be inserted (FK on base_procver_id).
--
-- Tables seeded:
--   1. base_processing_version  — the base version row
--   2. processing_version       — the processing version row
--   3. base_procver_of_procver  — links procver → base_procver at priority 0
--   4. procver_compact_id       — maps base_procver UUID → SMALLINT for spatial_id
--
-- Safe to run multiple times: all inserts use ON CONFLICT DO NOTHING.
-- UUIDs are generated at insert time via gen_random_uuid().
--
-- Usage:
--   kubectl cp scripts/seed_realtime_procver.sql <namespace>/<pod>:/fastdb/scripts/
--   kubectl exec -n <namespace> deploy/shell -- bash -c \
--     'PGPASSWORD=$(cat /secrets/pgpasswd) psql -h postgres -U postgres -d fastdb -f /fastdb/scripts/seed_realtime_procver.sql'

BEGIN;

-- 1. base_processing_version
INSERT INTO base_processing_version (description)
VALUES ('realtime')
ON CONFLICT DO NOTHING;

-- 2. processing_version
INSERT INTO processing_version (description)
VALUES ('realtime')
ON CONFLICT DO NOTHING;

-- 3. base_procver_of_procver (procver_id, base_procver_id, priority)
--    Links the realtime processing_version to the realtime base_processing_version.
INSERT INTO base_procver_of_procver (procver_id, base_procver_id, priority)
SELECT pv.id, bpv.id, 0
FROM processing_version pv, base_processing_version bpv
WHERE pv.description = 'realtime'
  AND bpv.description = 'realtime'
ON CONFLICT DO NOTHING;

-- 4. procver_compact_id
--    Maps the realtime base_procver UUID to an auto-incremented SMALLINT
--    used for spatial_id generation.
INSERT INTO procver_compact_id (procver_id)
SELECT id FROM base_processing_version
WHERE description = 'realtime'
ON CONFLICT DO NOTHING;

COMMIT;

-- Verify
\echo '=== base_processing_version ==='
SELECT id, description, created_at
FROM base_processing_version
WHERE description = 'realtime';

\echo '=== processing_version ==='
SELECT id, description, created_at
FROM processing_version
WHERE description = 'realtime';

\echo '=== base_procver_of_procver ==='
SELECT bpop.procver_id, bpop.base_procver_id, bpop.priority
FROM base_procver_of_procver bpop
JOIN processing_version pv ON pv.id = bpop.procver_id
WHERE pv.description = 'realtime';

\echo '=== procver_compact_id ==='
SELECT pci.procver_id, pci.compact_id
FROM procver_compact_id pci
JOIN base_processing_version bpv ON bpv.id = pci.procver_id
WHERE bpv.description = 'realtime';
