#!/usr/bin/env python3
"""Read-only CLI verifier for a bounded Guardian JSONL audit file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.guardian_audit import verify_audit_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="verify Guardian JSONL audit integrity")
    parser.add_argument("--input", required=True, type=Path, help="audit JSONL path")
    parser.add_argument("--max-bytes", type=int, default=None, help="optional inclusive byte limit")
    args = parser.parse_args()
    if args.max_bytes is not None and args.max_bytes < 0:
        parser.error("--max-bytes must be non-negative")
    result = verify_audit_file(args.input, max_bytes=args.max_bytes)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
