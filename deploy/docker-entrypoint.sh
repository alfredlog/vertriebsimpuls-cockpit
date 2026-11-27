#!/bin/sh
# Fresh volume: build the database once before the app starts. A failed first
# run removes the half-written file, so the next container start retries.
set -eu

if [ ! -f "$VIC_DB_PATH" ]; then
    echo "No database at $VIC_DB_PATH, running the daily pipeline once"
    if ! python -m vic.pipeline.daily_run --db "$VIC_DB_PATH" --evaluate; then
        rm -f "$VIC_DB_PATH" "$VIC_DB_PATH.wal"
        echo "Initial pipeline run failed" >&2
        exit 1
    fi
fi

exec "$@"
