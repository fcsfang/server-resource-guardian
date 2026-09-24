# Current Verified State

Updated: 2026-09-24

## Product status

| Outcome | Current evidence | Status |
| --- | --- | --- |
| Alerting and observation | Runtime, read-only Collector, CPU/memory/disk risk paths, status output, and Beszel integration remain in the supported tree. Pressure gate (PSI three-state) + audit slimming + gateway v2 verified live under a pinned memory-collapse storm: CRITICAL alerts and gate degraded-mode messages delivered in-window. | Implemented locally; storm-validated on WSL |
| External operator access | Two-layer acceptance contract (WSL class) **met on all six pass lines**: Layer-1 entry 40/40 + 20/20 (>=95%) after root-causing the swap-grind lockout and deploying the 4-layer platform hardening (earlyoom + sysctl + oom_score shields). Layer-2: MTTA 42s vs 1106-1160s blind (~26x), MTTD ~5-9s (<=10s), MTTI 0.07s (<=5s, 70x margin), observer effect 0.70x (<=1.5x), notification liveness under storm. | Two-layer contract met on WSL; x86_64 native confirmation pending |
| Authorized mitigation | A disposable Docker web target completed one exact, single-use, TERM-only action with audit. Host memory recovery and surviving-replica HTTP health were verified separately. This validates the safety mechanics only (one target, one signal, static authorization); the roadmap outcome-3 target is the full mitigation loop (dynamic identity, multi-target, verified escalation, business-health confirmation), which is not yet implemented. | Safety mechanics validated; full loop not implemented |

## Repository closeout

The 2026-09-23 reduction removes historical experiments, duplicate research notes, old presentation assets, obsolete demos, and superseded implementation paths. Git history remains the archive. The supported tree keeps the running product, deployment assets, current specifications, tests, the bounded rescue entry, and compact evidence summaries.

The current source compiles, Markdown contains no broken local links, shell scripts pass syntax checks, and the full regression suite passes. Raw SSH JSONL streams are local lab artifacts; only compact decision-bearing summaries belong in Git.

## Local lab facts

`guardian-t11-lite` is an ARM64 Ubuntu 22.04 disposable VM with 2 vCPUs, 2 GiB RAM, 8 GiB disk, systemd, cgroup v2, and Docker. Keep it stopped when not in use.

The completed Guardian-off search generated real OOM churn but still retained fresh SSH diagnosis. It did not meet the repeatable failure contract in [ROADMAP.md](ROADMAP.md), so the local SSH boundary route is closed as inconclusive. No further pressure escalation is justified on this VM.

The bounded rescue CLI and action helper are installed and exercise the intended safety rules: read-only diagnosis does not require Docker; an action requires one exact full ID, fresh identity, allowlist membership, a root-only short-lived authorization, and explicit confirmation; verification waits only for natural exit and never escalates to KILL.

The Docker closeout used an ARM64 nginx image transferred from the local cache, avoiding the blocked registry. Two disposable replicas ran in `workload.slice`. Guardian sent one TERM to the exact allowlisted target; it exited with code 0 and was not OOM-killed. Available memory increased by 204,443,648 bytes, the independent replica continued returning HTTP 200, and audit verification reported `recovered` with `escalation=none`. The temporary authorization, containers, image, and action-enabled configuration were removed or restored afterward.

## Current blockers and decisions

- **Cross-platform confirmation:** the two-layer acceptance was established on WSL2 (x86_64 via translation for the VM layer, native kernel 6.6+). An x86_64 native disposable retest remains the ROADMAP prerequisite for the cross-platform claim; the mechanism (swap-grind lockout, earlyoom fix) is understood and platform-configurable, so this is confirmation, not exploration.
- **Production:** no production connection, credential access, installation, pressure, or action is authorized.

## Evidence index

- [Current compact lab summary](tools/guardian-recovery-lab/latest-run.md)
- [Two-layer acceptance summary (2026-09-23/24, WSL)](tools/guardian-recovery-lab/results/2026-09-23-benefit/two-layer-acceptance-summary.json)
- [Layer-1 solved: earlyoom hardening stack](tools/guardian-recovery-lab/results/2026-09-23-benefit/layer1-solved-earlyoom.md)
- [Layer-2 findings + gateway v2](tools/guardian-recovery-lab/results/2026-09-23-benefit/gateway-v2-verify.md)
- [Observer effect bounded](tools/guardian-recovery-lab/results/2026-09-23-benefit/observer-effect-bounded.md)
- [Guardian-off SSH boundary search](tools/guardian-recovery-lab/results/2026-09-23/guardian-off-ssh-boundary-r1.json)
- [Multi-process OOM storm](tools/guardian-recovery-lab/results/2026-09-23/guardian-off-multiprocess-storm-r1.json)
- [MemoryHigh cleanup boundary](tools/guardian-recovery-lab/results/2026-09-23/memoryhigh-cleanup-boundary.json)
- [Bounded workload result](tools/guardian-recovery-lab/results/2026-09-23/oom-churn-boundary-r1.json)
- [Systemd fixture authorized-action audit](tools/guardian-recovery-lab/results/2026-09-23/guardian-authorized-fixture-audit.jsonl)
- [Docker business-object closeout](tools/guardian-recovery-lab/results/2026-09-23/docker-business-object-closeout.json)
