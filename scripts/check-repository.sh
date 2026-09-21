#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

required_files=(
  AGENTS.md
  README.md
  ROADMAP.md
  PROGRESS.md
  docs/01-requirements.md
  docs/03-architecture.md
  docs/06-safety-policy.md
)

for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    printf 'missing required file: %s\n' "$path" >&2
    exit 1
  fi
done

git diff --check
python3 -m unittest discover -s tests -v
