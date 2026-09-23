# 2026-09-23 Local Acceptance Summary

## Decision

The local 2-vCPU/2-GiB ARM64 VM validates the functional maintenance path and bounded-action safety rules. It does not establish a Guardian SSH-rescue advantage and is no longer a target for pressure escalation.

## Operator-access evidence

The Guardian-off search retained fresh external SSH diagnosis under every valid bounded run. A multi-process model caused 197 `system.slice` OOM kills and reduced available memory to about 46 MiB, yet the probe passed 15/15 samples at a two-second timeout and 10/10 at a 0.5-second timeout. One timeout in an earlier calibration did not repeat.

This does not meet the repeatable failure contract in [ROADMAP.md](../../ROADMAP.md). A Guardian-on comparison against those inputs would not measure product benefit.

## Bounded-action evidence

One allowlisted systemd fixture was identity-checked, matched to a root-only single-use authorization, and sent one TERM. The target cgroup disappeared, available memory increased, audit was retained, and verification recorded `escalation=none`. This proves the action safety boundary only; the fixture was not a Docker business object.

## Native-control boundary

The workload slice reaches `MemoryHigh` throttling before cgroup OOM. One cleanup calibration showed TERM remaining pending above `MemoryHigh`; temporarily raising the limit allowed exit, but Guardian verification now only observes and reports that condition. It never changes the limit or sends a second signal.

## Docker business-object closeout

An ARM64 nginx image was transferred from the local Docker cache because the VM registry path had timed out. Two disposable replicas ran in `workload.slice`; the target held about 194.7 MiB and the peer remained independently healthy.

One exact full container ID matched one fresh identity, one allowlist entry, and one root-only short-lived authorization. Guardian sent one TERM through Docker. The target exited with code 0 without OOM, available memory increased by 204,443,648 bytes, the peer continued returning HTTP 200, and verification recorded `recovered` with `escalation=none`. Observe-only configuration was restored and the authorization, containers, and transferred image were removed.

This closes the local Docker action path. The remaining product gap is the SSH advantage, which stays blocked until a representative disposable x86_64 environment is available.
