#!/usr/bin/env bash

# Disposable-only fixture for EXP-048.  It does not send a signal or create
# memory pressure: the first invocation exits with status 137 by itself, and
# the restarted invocation exits successfully after one read-only Observer
# sample.  systemd is the component under test.
set -u

python3 -m src.guardian_observer --once --audit-file "$GUARDIAN_EXP048_AUDIT"

if [[ ! -e "$GUARDIAN_EXP048_MARKER" ]]; then
    : > "$GUARDIAN_EXP048_MARKER"
    exit 137
fi

exit 0
