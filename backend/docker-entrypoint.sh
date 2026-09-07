#!/bin/sh
# Prepare the statements volume for the unprivileged app user, then hand over to gunicorn
# (its master keeps root for the bind and drops the workers to app:app via --user/--group).
set -e
DIR="${STATEMENTS_DIR:-/data/statements}"
mkdir -p "$DIR"
chown -R app:app "$DIR" 2>/dev/null || true
exec "$@"
