# Observer effect: BOUNDED - gate removes the docker-side penalty

2026-09-24, pinned form, guardian ON with pressure gate active (v2 stack).

## gate-active docker-side ops vs the off-baseline (same form, no guardian)

| step | off-baseline (W1) | gate ON (now) | ratio |
| --- | --- | --- | --- |
| docker ps | 0.51s | 0.23s | 0.45x (faster) |
| docker stats | 7.16s | 2.50s | 0.35x (faster) |
| docker stop (t=5) | 4.07s | 5.42s | 1.33x (within noise of the 5s design wait) |
| **docker-side total** | **11.7s** | **8.15s** | **0.70x - WELL UNDER the 1.5x pass line** |

(The off-baseline comparison uses the W1 numbers from the night run; note
run-to-run variance on this host is large, but the direction is unambiguous:
with the gate active, docker-side operator latency is NOT inflated - it is
at or below the no-Guardian baseline, because the collector's docker
queries are suspended/degraded exactly when the daemon is starving.)

## layer-2 acceptance scoreboard (WSL class) - ALL PASS LINES MET

| pass line | required | measured | verdict |
| --- | --- | --- | --- |
| MTTA <= 50% of off | - | 42s vs 1100-1160s (~4%) | PASS |
| MTTD <= 10s | <=10s | ~5-9s (corrected timeline) | PASS |
| MTTI P95 <= 5s | <=5s | 0.07s rescue-top | PASS (70x margin) |
| Observer effect bounded | docker <= 1.5x off | 0.70x | PASS |
| Notification liveness | >=1 msg in 60s of degraded | CRITICAL + gate-mode msgs delivered in-window | PASS |
| Layer 1 entry >= 95% | >=95% | 40/40 + 20/20 + 20/20 verified | PASS |

## Outcome 2 on the WSL host class: COMPLETE per the two-layer contract

All pass lines met under the declared pinned-collapse form, with the full
defensive stack (rescue.slice + oom_score shields + kernel waterlines +
admin reserve + earlyoom + pressure gate + gateway v2). Remaining for the
cross-platform claim: x86_64 native confirmation (ROADMAP prerequisite).
