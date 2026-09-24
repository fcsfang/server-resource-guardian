# Layer-2 night-run findings: three real defects found and root-caused

## Run results (with the corrected measurement)

The MTTD numbers in layer2_new_r1/r2.json (26135s) are MEASUREMENT BUGS:
journalctl --since @epoch matched a stale alert. The TRUE timeline from the
audit + gateway logs:

### run1 (swarm start ~10:46:5x)
- 10:47:04 CRITICAL sent (avail 0.0) -> MTTD ~5-9s from pressure start ✓
- earlyoom killed 9 hogs in the window; entry path stayed up
- rescue top: 0.07s one-shot locate ✓ (MTTI pass line <=5s: exceeded 70x)

### run2 (swarm start ~10:47:45)
- audit shows avail dropped to 0.0% at 10:47:49, stayed low 40+s
- state-machine replay (exact gateway logic on the real audit data): FIRED
  ok->critical at 02:47:51Z and critical->ok at 02:48:22Z - the gateway
  SHOULD have sent two messages
- gateway sent NOTHING in the window - a real defect, root-caused:

## Defect 1 (gateway): send-stall + copytruncate swallows events
The feishu send() has timeout=15s x3 retries + backoff; under the CPU storm
the API calls stalled ~3 minutes. Events that arrived during the stall sat
unread in events.jsonl; the 10:50:18 logrotate copytruncate then truncated
the file before the gateway reread them -> those events are gone forever.
FIX: decouple tail-reading from sending - read and parse continuously into
a bounded queue; send asynchronously; on copytruncate, parse-and-queue the
remaining backlog BEFORE resetting the offset.

## Defect 2 (measurement): alert-message text has no event linkage
message_text embeds only human timestamps; correlating a sent message to
its triggering event requires log forensics. FIX: include the event_id (or
sample_id) in the message tail.

## Defect 3 (gate): pressure_gate_transition marker never appeared
The orchestrator stamps transitions on the CURRENT event; under
degraded_observability states the marker lives only in the in-memory event
and in audit - but the gateway has no gate-key reader for the slimmed path
(gate refs in audit = 60, from collector metadata, not transitions).
FIX (next): gateway reads evidence.pressure_gate_transition + audit_fidelity
and reports gate state changes independently of resource levels.

## What PASSED in layer-2 despite the defects

- MTTI (rescue top one-shot locate): 0.07s - pass line 5s exceeded 70x
- gate audit refs present (60 in window): the audit stream carries gate data
- entry path stayed alive through BOTH runs (earlyoom active)
- environment cleaned: 0 containers, 0 hogs, stack healthy
