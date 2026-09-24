# Triple-resource pinned form: CPU + memory + disk saturation (2026-09-24)

## Experiment gate declaration

- **Product outcome evaluated**: Outcome 2, Layer 1 (entry capability under
  extreme pressure) + notification liveness (Outcome 1 evidence).
- **Success metric**: entry probe rate >=95% (contract pass line); docker-side
  ops <=1.5x the no-Guardian off-baseline.
- **Failure boundary**: if entry collapses, record as a hardening-stack gap.
  No escalation beyond the declared form (4x CPU spin + 1GiB fdatasync churn
  + 24x280MiB holder swarm).
- **Product decision it can change**: whether the defense stack needs CPU- or
  disk-side components (hypothesis going in: it does not - only memory kills).

## Form

| resource | injection | intensity |
| --- | --- | --- |
| CPU | 4x python spin (1/vCPU) | 900s |
| memory | 24x280MiB holder swarm (restart=on-failure) | 6.7GiB demand > 3.8+2GiB = pinned |
| disk | dd 1GiB fdatasync loop | 900s, sustained sync writes |

## Defended-arm results (stack: earlyoom + sysctls + oom_score shields + gate + gateway v2)

| check | r1 | r2 | verdict |
| --- | --- | --- | --- |
| entry probes | 19/20 (95%), 279-1036ms | 19/20 (95%), 253-1147ms | PASS at the line (both failures: single transport timeout, no cascade) |
| idle probes | 10/10 (136-486ms) | - | latency penalty ~2x vs idle, far from lockout |
| host down? | no | no | platform survived triple pin |
| alerts | 7 sent (CPU 98.1% CRITICAL, mem 0.8% CRITICAL, swap 100% CRITICAL, gate degraded-mode) | continued | liveness holds for all three resources |
| ops chain (11 steps) | interrupted (r1 session cut) | shell 0.85s + docker-side 6.8s | 0.58x off-baseline 11.7s - PASS |
| earlyoom kills | 1 (dd) | 161 (119x dd) | defect 1 (below) |
| kernel OOM | 5 (4 hogs + 1 user systemd) | ~8 (hogs + 1 user systemd) | defect 2 (below) |
| PSI pinned | cpu some 40/full 0; io some 13/full 0.5; mem some 19.5/full 9.9 | same shape | only memory reaches "full" pressure |

## Mechanism finding

CPU saturation queues but does not block (PSI cpu full=0); disk churn delays
but does not block (PSI io full~0.5). ONLY memory kills processes. The
memory-only defense stack (earlyoom) therefore covers the lethal axis of the
triple form - layer-1 entry held at 95% with ~2x latency penalty.

## Defects found (both real, both actionable)

1. **earlyoom vs respawn-style pressure (dd)**: the dd loop respawns after
   each kill; earlyoom killed it 119x in one window (churn), spending its
   attention there while kernel OOM covered the hogs. NOT a product defect -
   earlyoom's architecture does not address respawn sources. This is the
   target-3 (mitigation loop) requirement input: alert should identify the
   pressure source as respawn-type and prompt the admin to break the loop.
2. **kernel OOM killed the UID-1000 user systemd (oom_score_adj=100, WSL
   platform default)** while hogs (adj 0) were alive. Our shields cover the
   root-side control plane only. FIX: user@.service drop-in OOMScoreAdjust=0
   (balance, not shield), so the session manager ranks equal to ordinary
   processes and real hogs die first. One-line platform config.

## Baseline arm (undefended) - measured 2026-09-24 11:49-11:56 UTC

Same form, stack OFF: earlyoom stopped, admin_reserve back to 8MB,
swappiness 60, Guardian trio off (sshd -1000 shield retained - restarting
sshd is prohibited; the dominant failure mode is transport-stage, which the
shield does not touch, so comparability with the 19/40 memory-only baseline
holds).

| check | baseline (undefended) | defended | ratio / verdict |
| --- | --- | --- | --- |
| **entry probes** | **8/20 (40%)** | 19/20 (95%) | **2.4x entry-rate gap; contract pass line met only with the stack** |
| failed probe latency | transport timeouts up to 7.5s | 1 timeout at 2.98s | failure depth also bounded by the stack |
| successful probe latency | 1381-3615ms | 253-1147ms | **~3.3x slower undefended** |
| docker_stats (ops chain) | **241.3s** | 2.69s | **90x** (EXP-088's observer effect, now on the baseline arm) |
| docker_ps (ops chain) | 17.8s | 0.41s | 43x |
| journalctl (ops chain) | 6.2s | 0.60s | 10x |
| PSI memory (pinned) | some 61 / **full 45.3** | some 19.5 / full 9.9 | **undefended hits PSI-mem full 45% - the grind the stack removes** |
| PSI cpu (pinned) | some 73 / full 0 | some 40 / full 0 | CPU queues in both; never lethal |
| kernel OOM in window | 8 kills (incl. user systemd) | hogs + 1 user systemd | baseline: kernel thrashes and kills wide |
| host down | no | no | platform survived either way |

### The one-line answer to "what would the baseline look like?"

**40% entry (8/20), 3.3x slower when it does connect, 241 seconds for one
`docker stats`, memory PSI-full at 45%.** The undefended triple form lands
below even the memory-only baseline (52.5%): adding CPU + disk pressure
deepens the same swap-grind lockout - pageouts now compete with the fdatasync
storm for the same disk bandwidth. Guardian + the platform stack is the
difference between 8/20 and 19/20 entry, 241s and 2.7s operator stats, and
PSI-full 45% vs 10%.

Baseline data: /root/triple_baseline_r1.json (WSL local); key numbers above.

## Defect 2 fix (applied and verified 2026-09-24 11:56 UTC)

`/etc/systemd/system/user@.service.d/oom-balance.conf` -> OOMScoreAdjust=0.
Verified live: user@1000 manager oom_score_adj 100 -> 0 (ranks equal to
ordinary processes; kernel kills real hogs first). Control-plane shields
unchanged (-900 runtime / -800 collector+gateway / -1000 sshd). Defect 1
recorded as target-3 requirement input, no code change by design.
