#!/bin/bash
# Dog scanner healthcheck.
#
# Exits 0 if the watcher has run within the last MAX_STALE_SECS seconds.
# Exits 1 if the heartbeat is stale or missing — systemd or cron can use
# that non-zero exit to restart the service.
#
# Usage:
#   ./healthcheck.sh                      # silent unless stale
#   ./healthcheck.sh --restart-if-stale   # also: systemctl restart on stale
#
# Drop-in cron line (every 5 min, restart if needed):
#   */5 * * * * /home/claude-dev/dog-scanner/healthcheck.sh --restart-if-stale

set -eu

HEARTBEAT=/home/claude-dev/dog-scanner/state/watcher_heartbeat.json
MAX_STALE_SECS=${MAX_STALE_SECS:-1500}   # 25 min — 2.5× the 10-min cycle

if [ ! -f "$HEARTBEAT" ]; then
    echo "STALE: heartbeat file missing: $HEARTBEAT" >&2
    exit 1
fi

# Parse ISO timestamp → epoch seconds
last_iso=$(python3 -c "import json; print(json.load(open('$HEARTBEAT'))['last_cycle_utc'])" 2>/dev/null)
if [ -z "$last_iso" ]; then
    echo "STALE: cannot parse heartbeat" >&2
    exit 1
fi

last_epoch=$(date -u -d "$last_iso" +%s)
now_epoch=$(date -u +%s)
age=$((now_epoch - last_epoch))

if [ "$age" -gt "$MAX_STALE_SECS" ]; then
    echo "STALE: last watcher cycle was ${age}s ago (max ${MAX_STALE_SECS}s)" >&2
    if [ "${1:-}" = "--restart-if-stale" ]; then
        echo "Triggering systemctl restart dog-scanner..." >&2
        sudo /usr/bin/systemctl restart dog-scanner
    fi
    exit 1
fi

# Healthy — silent by default
exit 0
