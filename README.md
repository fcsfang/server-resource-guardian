# Server Resource Guardian

Guardian helps administrators see dangerous CPU, memory, or disk pressure, retain a practical path for SSH diagnosis and manual recovery, and - inside an explicit authorization boundary - assist them through a complete mitigation loop against the pressure source: dynamic target identity, verified escalation, and separate host and business-health recovery confirmation, with every step audited.

## Product boundaries

- Beszel provides monitoring, history, display, and alerts. It never triggers host actions.
- Guardian is observe-only by default. Any action requires an explicit authorization boundary (scope of targets and permitted escalation), identity revalidation, and an audit record. Today's implemented slice of outcome 3 is the single-target TERM path; the full loop (multi-target, verified escalation, business-health confirmation) is the roadmap target.
- Guardian never sends KILL automatically, never reboots a host, and never deletes business data. Escalation beyond the granted boundary requires new authorization.
- Same-host protection cannot guarantee SSH through kernel, device, network, power, or severe root-filesystem failure. Production recovery still needs an out-of-band console.

## Repository map

| Path | Purpose |
| --- | --- |
| `src/` | Runtime, read-only collection, risk interpretation, authorization, audit, and recovery verification |
| `scripts/` | Installation and operator entry points |
| `deploy/` | systemd and deployment assets |
| `config/` | Credential-free configuration examples and schema |
| `tests/` | Product and deployment regression tests |
| `tools/guardian-recovery-lab/` | Disposable-lab probes and compact acceptance evidence |
| `docs/` | Current architecture and safety contracts |

Historical experiments and presentation assets were removed during the 2026-09-23 repository reduction. Git history remains the archive; deleted research artifacts are not task sources.

## Start here

Read [ROADMAP.md](ROADMAP.md) for the acceptance contract and [PROGRESS.md](PROGRESS.md) for the current verified state. Do not infer production readiness from old experiments or a local VM result.

## Development verification

Use a virtual environment and install the single development dependency:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
pytest -q
python3 -m compileall -q src scripts tools/guardian-recovery-lab
git diff --check
```

## Installation

The x86_64 installer defaults to a read-only plan. Applying it is limited to an explicitly authorized non-production Ubuntu host:

```bash
./scripts/install-guardian-x86.sh
python3 scripts/guardian-x86-preflight.py
sudo ./scripts/install-guardian-x86.sh --apply --environment x86-observe
sudo guardian-status
```

See [deploy/guardian-x86/README.md](deploy/guardian-x86/README.md) for effects and rollback.
