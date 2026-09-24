# Latest run: 2026-09-23/24 - two-layer acceptance on WSL (outcome 2)

## Decision

Outcome 2 (operator access) on the WSL host class is **COMPLETE per the two-layer contract**: all six pass lines met. Layer 1 entry failure under the pinned collapse form was root-caused (swap-grind lockout) and SOLVED by a 4-layer platform hardening stack (earlyoom + sysctl + oom_score shields) - 40/40 + 20/20 probes vs the >=95% pass line. Layer 2 benefit is now quantified: MTTA 42s vs 1106-1160s blind (~26x), MTTD ~5-9s, MTTI 0.07s, notification liveness under storm, observer effect 0.70x (EXP-088's 21x degradation eliminated by the pressure gate + gateway v2).

The remaining gap for the cross-platform claim is an x86_64 native confirmation (ROADMAP prerequisite, blocked on a disposable environment).

## Evidence

- [Two-layer acceptance summary (final)](results/2026-09-23-benefit/two-layer-acceptance-summary.json) - scoreboard with all six pass lines
- [Layer-1 solved: earlyoom stack](results/2026-09-23-benefit/layer1-solved-earlyoom.md) - root cause + decisive test table
- [Layer-2 findings](results/2026-09-23-benefit/layer2-findings.md) - gateway defects found and fixed (v2)
- [Gateway v2 verification](results/2026-09-23-benefit/gateway-v2-verify.md) - CRITICAL delivered under storm, gate-mode visibility
- [Observer effect bounded](results/2026-09-23-benefit/observer-effect-bounded.md) - 8.15s vs 11.7s baseline = 0.70x
- [Night run report](results/2026-09-23-benefit/night-run-report.md) - unattended-run log and incidents

## Deployable assets committed

- `deploy/guardian/transport-defense-sysctl.conf` - admin reserve 256MB, swappiness 10, earlyoom deploy notes (incl. the systemd unit `$ARGS` literal pitfall and the earlyoom-vs-Guardian action boundary)
- `deploy/guardian/oom-defense/` - sshd/runtime/collector+gateway oom_score drop-ins
- `deploy/guardian/rescue.slice` - protected cgroup for the control plane (doubles as per-cgroup TCP socket protection on kernel 6.6+)
- `tools/feishu_alert_gateway.py` - gateway v2: decoupled reader/sender, bounded queue, copytruncate backlog drain, event_id in messages, gate-key + degraded-mode visibility
- `src/guardian_pressure_gate.py` - PSI three-state gate + audit slimming (full/summary/minimal)
- `tools/collapse-pressure-model/` - the reusable pinned-form pressure kit (24x280MiB holder swarm)

## Previous run (t11-lite ARM64)

The 2026-09-23 local acceptance on `guardian-t11-lite` validated the bounded-action safety mechanics (Docker business object closeout) but did not establish an SSH-rescue advantage - Guardian-off never failed repeatably there. Details in [results/2026-09-23/](results/2026-09-23/) and the archive below.
