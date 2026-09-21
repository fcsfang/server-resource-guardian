#!/usr/bin/env python3
"""Print the installed Guardian status without changing system state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_status_module():
    install_root = Path(os.environ.get("GUARDIAN_INSTALL_ROOT", "/opt/server-resource-guardian"))
    sys.path.insert(0, str(install_root))
    from src.guardian_status import build_status, exit_code, format_status

    return build_status, exit_code, format_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Guardian administrator status")
    parser.add_argument("--json", action="store_true", help="emit the structured status document")
    parser.add_argument("--config", type=Path, default=Path("/etc/guardian/guardian.json"))
    parser.add_argument("--readiness", type=Path, default=Path("/run/guardian-runtime/ready"))
    parser.add_argument("--audit", type=Path, default=Path("/var/lib/guardian/runtime/audit/events.jsonl"))
    args = parser.parse_args(argv)
    build_status, status_exit_code, format_status = _load_status_module()
    value = build_status(config_path=args.config, readiness_path=args.readiness, audit_path=args.audit)
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(format_status(value))
    return status_exit_code(value)


if __name__ == "__main__":
    raise SystemExit(main())
