#!/bin/bash
# =============================================================================
# BOUNDARY MATCHING SIMULATION
# =============================================================================
# This script runs the full end-to-end boundary matching demonstration:
#   1. Seeds boundary test objects to MongoDB
#   2. Imports them into PostgreSQL via source_importer
#   3. Runs SQL queries demonstrating boundary detection
#
# Usage:
#   ./scripts/run_boundary_simulation.sh [namespace]
#
# Arguments:
#   namespace - Kubernetes namespace (default: ccosta-dev)
#
# Prerequisites:
#   - kubectl configured and authenticated
#   - Access to the Kubernetes cluster
# =============================================================================

set -e

NAMESPACE="${1:-ccosta-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================================="
echo "BOUNDARY MATCHING SIMULATION"
echo "============================================================================="
echo "Namespace: $NAMESPACE"
echo ""

# -----------------------------------------------------------------------------
# Step 1: Get pod names
# -----------------------------------------------------------------------------
echo "=== Finding pods ==="
SHELL_POD=$(kubectl get pods -n "$NAMESPACE" -l app=shell -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
POSTGRES_POD=$(kubectl get pods -n "$NAMESPACE" -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$SHELL_POD" ]; then
    echo "ERROR: Could not find shell pod in namespace $NAMESPACE"
    exit 1
fi

if [ -z "$POSTGRES_POD" ]; then
    echo "ERROR: Could not find postgres pod in namespace $NAMESPACE"
    exit 1
fi

echo "Shell pod: $SHELL_POD"
echo "Postgres pod: $POSTGRES_POD"
echo ""

# -----------------------------------------------------------------------------
# Step 2: Copy seed script to cluster
# -----------------------------------------------------------------------------
echo "=== Copying seed script to cluster ==="
kubectl cp "$SCRIPT_DIR/seed_boundary_objects.py" "$NAMESPACE/$SHELL_POD:/tmp/seed_boundary_objects.py"
echo "Done."
echo ""

# -----------------------------------------------------------------------------
# Step 3: Run seed script
# -----------------------------------------------------------------------------
echo "=== Seeding boundary objects to MongoDB ==="
kubectl exec -n "$NAMESPACE" "$SHELL_POD" -- python3 /tmp/seed_boundary_objects.py
echo ""

# -----------------------------------------------------------------------------
# Step 4: Import to PostgreSQL
# -----------------------------------------------------------------------------
echo "=== Importing to PostgreSQL ==="
kubectl exec -n "$NAMESPACE" "$SHELL_POD" -- python -m services.source_importer -p realtime -c test_alerts
echo ""

# -----------------------------------------------------------------------------
# Step 5: Run demo queries
# -----------------------------------------------------------------------------
echo "=== Running boundary matching demo ==="
kubectl cp "$SCRIPT_DIR/demo_boundary_matching.sql" "$NAMESPACE/$POSTGRES_POD:/tmp/demo_boundary_matching.sql"
kubectl exec -n "$NAMESPACE" "$POSTGRES_POD" -c postgres -- psql -U postgres -d fastdb -f /tmp/demo_boundary_matching.sql
echo ""

echo "============================================================================="
echo "SIMULATION COMPLETE"
echo "============================================================================="
