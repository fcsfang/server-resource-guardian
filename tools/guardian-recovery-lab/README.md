# Guardian Recovery Lab

This directory supports two bounded disposable-lab outcomes: external operator access and one authorized TERM-only stop. It is not a general stress-test toolkit.

## Decision gate

Before every pressure run, name the product outcome, success metric, failure boundary, and product decision. The SSH pass/fail contract is defined in the repository [ROADMAP](../../ROADMAP.md). Do not run Guardian-on until the exact Guardian-off boundary is repeatable.

## Tools

- `probe_ssh.py` opens a fresh SSH session for every sample and records transport, shell, and each named diagnostic stage independently.
- `pressure.py` creates bounded CPU and resident-memory workers for disposable calibration only.
- `storm_worker.py` creates independent memory, CPU, or bounded fsync workers for a disposable multi-process model.

The pressure tools do not authorize actions, select targets, or clean up an uncertain host. Keep an out-of-band console available and stop when cleanup is uncertain.

## Evidence policy

Raw JSONL probe streams stay local and are ignored by Git. Commit only compact JSON or Markdown summaries that state parameters, outcome, safety restoration, and the product decision. Never convert calibration into effectiveness evidence.

## External probe example

```bash
python3 tools/guardian-recovery-lab/probe_ssh.py \
  --host VM_IP --user guardian-maint --identity ~/.ssh/guardian-lab \
  --duration 30 --interval 1 --timeout 2 \
  --check resources='free -m' \
  --check guardian-status='guardian-rescue status' \
  --check guardian-top='guardian-rescue top --limit 3' \
  --output /tmp/guardian-probe.jsonl
```

Each requested stage must be present and return zero for the complete probe to pass.
