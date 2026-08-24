#!/usr/bin/env bash
# DEPRECATED: retained temporarily for the previous local deployment workflow.
set -euo pipefail

echo "WARNING: this script is deprecated." >&2
echo "Use these scripts for new local installations:" >&2
echo "  ./helm/scripts/create-local-cluster.sh" >&2
echo "  ./helm/scripts/install-local-fastdb.sh" >&2
echo >&2

FASTDB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER="fastdb-local"
NAMESPACE="$CLUSTER"
CONTEXT="kind-$CLUSTER"
EXTERNAL_URL="http://localhost:8080/"
DOCKER_ARCHIVE="${DOCKER_ARCHIVE:-ghcr.io/lsstdesc}"
DOCKER_VERSION="${DOCKER_VERSION:-test20260428}"

for command_name in docker helm kind kubectl sed; do
  command -v "$command_name" >/dev/null || {
    echo "Error: $command_name is required." >&2
    exit 1
  }
done

cd "$FASTDB_DIR"

if ! kind get clusters | grep -Fxq "$CLUSTER"; then
  echo "Creating Kind cluster $CLUSTER..."
  sed "s|\${PWD}|$FASTDB_DIR|g" admin/local/kind-config.yaml |
    kind create cluster --name "$CLUSTER" --config -
fi

echo "Building local shell image..."
docker compose build createdb

echo "Building FASTDB install/ with external URL $EXTERNAL_URL..."
docker compose run --rm --entrypoint "" makeinstall /bin/bash -ec "
  touch aclocal.m4 configure
  find . -name Makefile.am -exec touch {} \\\;
  find . -name Makefile.in -exec touch {} \\\;
  ./configure \\
    --with-installdir=/fastdb \\
    --with-smtp-server=mailhog \\
    --with-smtp-port=1025 \\
    --with-external-url=$EXTERNAL_URL \\
  && make install
"

echo "Building local container images..."
docker compose build postgres mongodb createdb webap queryrunner

echo "Loading images into Kind..."
for image_name in postgres mongodb shell webap query-runner; do
  kind load docker-image --name "$CLUSTER" \
    "$DOCKER_ARCHIVE/fastdb-$image_name:$DOCKER_VERSION"
done

echo "Deploying Helm chart..."
./scripts/helm-deploy.sh "$NAMESPACE" ./helm/fastdb/values-local.yaml \
  --skip-build --context "$CONTEXT"

kubectl --context "$CONTEXT" get pods -n "$NAMESPACE"
echo "FASTDB:  http://localhost:8080"
echo "MailHog: http://localhost:8025"
