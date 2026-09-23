# Layer-1 collapse entry results (night run 2026-09-23, segment form)

## Guardian ON (new build 753a454, with OOM defense: sshd -1000, runtime -900, collector/gateway -800)

pressure: 24x280MiB holder swarm, restart=on-failure; pinned state
  RAM free 297Mi -> 47Mi, swap 1985/2048 -> 2048/2048, 15 OOM kills during window

run1: 10/20 ok, 10 failed, ALL failures at transport stage
      elapsed_ms 188.9 - 6965.7 (timeouts >5000ms)
run2:  9/20 ok, 11 failed, ALL failures at transport stage
      elapsed_ms 200.9 - 8446.5

=> entry failure rate 52.5% aggregate (19/40) - FAR beyond the contract's
   20% repeatable-boundary line, and the failing stage is identified:
   TCP transport (connect/auth timeout), NOT shell/PAM/fork.

## interpretation (provisional until OFF control lands)

- The pinned form DOES close the management door from outside - repeatably.
- sshd carrying oom_score_adj=-1000 did NOT prevent transport-stage failures:
  sshd itself was protected, but the door failure sits deeper (VM-level
  memory pressure stalls the WSL vsock + loopback TCP accept path). This is
  consistent with the 20:08-20:26 incident where the entire Windows<->WSL
  channel died.
- Layer-1 pass line (>=95%) is NOT met by Guardian-on under this form.
  Guardian-on also does not LOWER entry vs off - the off control run will
  settle that comparison.

## next
- off control (guardian stopped, same form) -> attribution
- then layer-2 MTTA runs (new build) while the swarm form is fresh
