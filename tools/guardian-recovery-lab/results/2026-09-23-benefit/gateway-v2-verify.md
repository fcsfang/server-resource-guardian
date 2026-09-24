# Gateway v2 verification: PASS on all three defect fixes

2026-09-24 10:58-10:59, same pinned form (24x280MiB swarm), gateway v2 live.

## defect-fix verification

1. **Alert delivery under storm (was: swallowed by send-stall + copytruncate)**
   - CRITICAL memory alert SENT at 10:59:18 (可用 7.8%), CRITICAL swap at
     10:59:18 (已用 99.4%) - delivered DURING the storm, with event_id tags
     [9012f08f], [ee475258] for forensics.
2. **Gate degraded-mode visibility (was: silent degradation)**
   - "压力门控: WARNING 观测降级模式 (minimal)" sent at 10:58:16 [3bb15710]
     - the stream announces it is degraded BY DESIGN, before the resource
       alerts even fire.
3. **Entry capability with the full stack** - 20/20 probes, 122-671ms.

## consolidated layer-1/2 acceptance state (WSL class)

- Layer 1: 40/40 (earlyoom stack) + 20/20 (v2 verify) - pass line >=95% MET
- Layer 2 MTTD: ~5-9s (run1 corrected timeline); pass line <=10s MET
- Layer 2 MTTI: 0.07s rescue-top one-shot; pass line <=5s exceeded 70x
- Notification liveness: CRITICAL + gate-mode messages delivered under the
  pinned storm - pass condition MET
- Observer effect: not yet bounded-measured (docker-side ops <=1.5x off) -
  the one remaining unmeasured pass line; the off-baseline numbers exist
  from the previous runs (W1: docker ps 0.51s/stats 7.16s)

## remaining

- observer-effect bounded measurement (one more storm window, ops chain)
- PROGRESS.md + latest-run.md updates
- x86 native confirmation (platform-independent validation)
