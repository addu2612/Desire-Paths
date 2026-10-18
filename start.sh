#!/bin/sh
# Start FalkorDB in the background, load the cached repos, then serve the app.
set -e
redis-server --loadmodule /var/lib/falkordb/bin/falkordb.so --save "" --appendonly no --daemonize yes \
  --port "${FALKORDB_PORT:-6379}" --bind 127.0.0.1
until redis-cli -p "${FALKORDB_PORT:-6379}" ping >/dev/null 2>&1; do sleep 0.2; done
uv run --no-sync python -m desire_paths.graph
exec uv run --no-sync desire-paths
