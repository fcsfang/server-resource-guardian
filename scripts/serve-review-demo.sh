#!/usr/bin/env bash
set -euo pipefail

# Local-only, read-only review page. No credentials, network API, or executor is used.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
port="${1:-8765}"

case "$port" in
  ''|*[!0-9]*)
    printf 'port must be numeric\n' >&2
    exit 2
    ;;
esac

exec python3 -m http.server "$port" --directory "$repo_root/demo/guardian-beszel-review"
