"""systemd ExecStartPre checks for the Guardian local service package."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .guardian_config import ConfigError, load_config, safe_defaults
from .guardian_emergency_shedding import emergency_policy_digest, emergency_shedding_policy_from_config


PREFLIGHT_SCHEMA = "guardian.service.preflight.v1"


def _check_parent(path: Path, *, writable: bool) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise OSError(f"parent_directory_missing:{parent}")
    if writable and not os.access(parent, os.W_OK):
        raise OSError(f"parent_directory_not_writable:{parent}")


def _check_existing_file(path: Path, *, writable: bool) -> None:
    if not path.is_file():
        raise OSError(f"required_file_missing:{path}")
    if not os.access(path, os.R_OK):
        raise OSError(f"required_file_not_readable:{path}")
    if writable and not os.access(path, os.W_OK):
        raise OSError(f"required_file_not_writable:{path}")


def run(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config) if args.config else safe_defaults()
    policy = emergency_shedding_policy_from_config(config)
    _check_parent(args.state_db, writable=True)
    if args.role == "runtime":
        if args.outbox_db is None or args.audit_file is None:
            raise ValueError("runtime_paths_required")
        _check_parent(args.outbox_db, writable=True)
        _check_parent(args.audit_file, writable=True)
    elif args.role == "broker":
        if args.socket is None:
            raise ValueError("broker_socket_required")
        _check_existing_file(args.state_db, writable=True)
        _check_parent(args.socket, writable=True)
    if args.mode is not None and args.mode != config.mode:
        # CLI overrides are allowed by the Runtime, but both values must still
        # be one of the validated modes.  This prevents a typo from reaching
        # the long-running process.
        if args.mode not in {"observe", "simulate", "enforce"}:
            raise ValueError("mode_invalid")
    if args.role == "broker" and config.mode == "enforce" and not policy.enabled:
        raise ValueError("broker_enforce_requires_enabled_policy")
    return {
        "schema": PREFLIGHT_SCHEMA,
        "role": args.role,
        "status": "PASS",
        "config_digest": config.config_digest,
        "policy_digest": emergency_policy_digest(policy),
        "mode": args.mode or config.mode,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guardian systemd preflight")
    parser.add_argument("--role", choices=("runtime", "broker"), required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--outbox-db", type=Path)
    parser.add_argument("--audit-file", type=Path)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--mode")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))
    except (ConfigError, OSError, ValueError) as exc:
        print(json.dumps({"schema": PREFLIGHT_SCHEMA, "status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
