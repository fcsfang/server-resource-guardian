# Night plan: layer-2 rerun (staged, ready to execute)

## Preflight (before starting)
1. Confirm channel: wsl.exe -d Ubuntu -u root -- echo alive
2. Confirm clean state: 0 containers, 0 hogs, guardian stack active
3. Confirm build: slim_audit_event present in /opt/server-resource-guardian/src

## Sequence (each step fully self-contained, data persisted immediately)

### Step A - layer2 new-build run 1
  bash /mnt/c/Users/Chuanshan.Fang/AppData/Local/Temp/task5_l2_new.sh 1
  -> /root/layer2_new_r1.json  (MTTD + rescue-top MTTI + target)

### Step B - cleanup between runs (restart-policy safe)
  docker update --restart=no $(docker ps -aq) 2>/dev/null
  docker rm -f $(docker ps -aq) 2>/dev/null; pkill -9 -f 'python3 /hog.py'

### Step C - layer2 new-build run 2
  bash /mnt/c/Users/Chuanshan.Fang/AppData/Local/Temp/task5_l2_new.sh 2
  -> /root/layer2_new_r2.json

### Step D - docker-side ops latency (observer effect bound check)
  start swarm -> run 11-step ops chain via in-WSL loopback (timed)
  compare docker ops vs the off-baseline from yesterday's W2/W1 runs
  key check: docker steps <= 1.5x off-baseline with gate active

### Step E - notification liveness
  during the swarm window: verify >=1 feishu message within 60s of
  the gate entering degraded/suspended (journalctl -u feishu-gateway)

### Step F - evidence + commit
  fill two-layer-acceptance-summary.json layer2.guardian_on_new_build
  update latest-run.md + PROGRESS.md benefit line
  commit+push

## Interpretation guardrails
- If channel dies mid-run: the step's data file may be partial; the next
  step always starts with cleanup (Step B pattern), never assumes state.
- If MTTD > 10s with gate marker active: check whether the transition
  event (evidence.pressure_gate_transition) reached the audit stream -
  if yes, the gateway path is the bottleneck; if no, the gate evaluation
  timing is the bottleneck. Record which.
- Judge observer effect ONLY on docker-side steps (ps/free/dmesg are
  shell-side and were 2-4x even in EXP-088 - expected, not a gate failure).
