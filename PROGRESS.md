# Current Verified State

Updated: 2026-09-23

## Product status

| Outcome | Current evidence | Status |
| --- | --- | --- |
| Alerting and observation | Runtime, read-only Collector, CPU/memory/disk risk paths, status output, and Beszel integration remain in the supported tree. | Implemented locally; not production-validated |
| External operator access | Fresh SSH diagnosis passed under bounded pressure and during a real OOM storm on `guardian-t11-lite`. Guardian-off did not fail repeatably, so no Guardian advantage can be measured there. | Functional path passed; benefit unproven |
| Authorized mitigation | A disposable systemd fixture completed one allowlisted, single-use, TERM-only action with audit and host recovery verification. | Safety boundary passed; Docker business object pending |

## Repository closeout

The 2026-09-23 reduction removes historical experiments, duplicate research notes, old presentation assets, obsolete demos, and superseded implementation paths. Git history remains the archive. The supported tree keeps the running product, deployment assets, current specifications, tests, the bounded rescue entry, and compact evidence summaries.

The current source compiles, Markdown contains no broken local links, shell scripts pass syntax checks, and the full regression suite passes. Raw SSH JSONL streams are local lab artifacts; only compact decision-bearing summaries belong in Git.

## Local lab facts

`guardian-t11-lite` is an ARM64 Ubuntu 22.04 disposable VM with 2 vCPUs, 2 GiB RAM, 8 GiB disk, systemd, cgroup v2, and Docker. Keep it stopped when not in use.

The completed Guardian-off search generated real OOM churn but still retained fresh SSH diagnosis. It did not meet the repeatable failure contract in [ROADMAP.md](ROADMAP.md), so the local SSH boundary route is closed as inconclusive. No further pressure escalation is justified on this VM.

The bounded rescue CLI and action helper are installed and exercise the intended safety rules: read-only diagnosis does not require Docker; an action requires one exact full ID, fresh identity, allowlist membership, a root-only short-lived authorization, and explicit confirmation; verification waits only for natural exit and never escalates to KILL.

## Current blockers and decisions

- **Docker business-object evidence:** the VM previously had no local image and Docker Hub timed out. The approved closeout route is offline transfer of an existing local ARM64 nginx image, followed by the exact two-replica TERM-only loop in the roadmap.
- **SSH rescue advantage:** a representative disposable x86_64 environment is not currently available. Until it is, the product claim remains best-effort operator access, not a rescue guarantee or measured advantage.
- **Production:** no production connection, credential access, installation, pressure, or action is authorized.

## Evidence index

- [Current compact lab summary](tools/guardian-recovery-lab/latest-run.md)
- [Guardian-off SSH boundary search](tools/guardian-recovery-lab/results/2026-09-23/guardian-off-ssh-boundary-r1.json)
- [Multi-process OOM storm](tools/guardian-recovery-lab/results/2026-09-23/guardian-off-multiprocess-storm-r1.json)
- [MemoryHigh cleanup boundary](tools/guardian-recovery-lab/results/2026-09-23/memoryhigh-cleanup-boundary.json)
- [Bounded workload result](tools/guardian-recovery-lab/results/2026-09-23/oom-churn-boundary-r1.json)
- [Systemd fixture authorized-action audit](tools/guardian-recovery-lab/results/2026-09-23/guardian-authorized-fixture-audit.jsonl)
