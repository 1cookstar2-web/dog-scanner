#!/usr/bin/env bash
# Refresh project-bundle/ as a 1:1 carbon copy of the live project.
# Every top-level item except project-bundle/ and this script itself is
# copied in place, preserving permissions and timestamps.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$ROOT/project-bundle"
SELF="$(basename "$0")"

rm -rf "$BUNDLE"
mkdir "$BUNDLE"

shopt -s dotglob nullglob
for item in "$ROOT"/*; do
    name="$(basename "$item")"
    [[ "$name" == "project-bundle" ]] && continue
    [[ "$name" == "$SELF" ]] && continue
    cp -a "$item" "$BUNDLE/"
done
shopt -u dotglob nullglob

# Verify parity
diff \
    <(cd "$ROOT" && find . -type f -not -path './project-bundle/*' -not -name "$SELF" -exec sha256sum {} \; | sort -k2 | sed 's| \./| |') \
    <(cd "$BUNDLE" && find . -type f -exec sha256sum {} \; | sort -k2 | sed 's| \./| |') \
    && echo "Bundle is a 1:1 carbon copy of $ROOT"
