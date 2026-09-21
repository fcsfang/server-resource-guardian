#!/usr/bin/env python3
"""Render a non-mutating Rescue Plane install/diagnose/disable/rollback plan.

This is intentionally a planner, not an installer. It has no subprocess,
systemd, Docker, or Multipass calls. An operator must review the rendered plan
and execute the Runbook manually inside an approved maintenance window.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEPLOY_FILES = (
    "deploy/guardian/rescue.slice",
    "deploy/guardian/workload.slice",
    "deploy/guardian/guardian-runtime.service",
    "deploy/guardian/guardian.tmpfiles",
)

COMMON_GATES = (
    "approved disposable VM or maintenance window",
    "verified out-of-band console or BMC/cloud-console path",
    "fresh backup/snapshot of unit files, config, and /var/lib/guardian",
    "root filesystem free-space and journald retention review",
    "Broker marker absent; default mode remains observe",
)

PLANS: dict[str, tuple[str, ...]] = {
    "install": (
        "review the dependency graph: SSH, network/DNS, logind/user.slice, journald, Docker/containerd",
        "static-verify the four deployment files and run preflight in a temporary state directory",
        "install files and create tmpfiles only after the gates are recorded",
        "reload systemd, start observe Runtime, and verify readiness plus read-only diagnostics",
    ),
    "diagnose": (
        "inspect unit state, cgroup membership, readiness, journal usage, root free space, and audit bounds",
        "check SSH/session, network/DNS, logind/user.slice, Docker/containerd control-plane health",
        "run only the fixed read-only Rescue Plane probe; preserve failure evidence",
    ),
    "disable": (
        "stop and disable Guardian Runtime only, then confirm the Broker marker and socket are absent",
        "verify Docker, containerd, Beszel, SSH, network, logind, and journald were not changed",
        "retain state and audit data for review; do not delete business files automatically",
    ),
    "rollback": (
        "keep Broker disabled and restore the previously reviewed unit/config/state backup",
        "re-run static verification and preflight before starting observe Runtime",
        "verify readiness, reconciliation, SSH/session access, and read-only control-plane diagnostics",
    ),
}


def render_plan(action: str, *, repository: str = ".") -> dict[str, Any]:
    if action not in PLANS:
        raise ValueError(f"unsupported_action:{action}")
    return {
        "schema": "guardian.rescue_plan.v1",
        "mode": "dry-run",
        "action": action,
        "repository": repository,
        "steps": list(PLANS[action]),
        "deployment_files": list(DEPLOY_FILES),
        "gates": list(COMMON_GATES),
        "changes_applied": False,
        "systemd_mutation_invoked": False,
        "docker_mutation_invoked": False,
        "multipass_mutation_invoked": False,
        "requires_operator_review": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a non-mutating Rescue Plane plan")
    parser.add_argument("action", choices=sorted(PLANS))
    parser.add_argument("--repository", default=".")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = render_plan(args.action, repository=args.repository)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
