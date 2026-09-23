# Ubuntu x86_64 Observe Install

This installer supports Ubuntu 22.04 x86_64 with systemd and cgroup v2. It installs the Runtime and read-only Collector; automatic actions remain disabled. The repository has not established production readiness or a general SSH-rescue guarantee.

```bash
./scripts/install-guardian-x86.sh
python3 scripts/guardian-x86-preflight.py
sudo ./scripts/install-guardian-x86.sh --apply --environment x86-observe
sudo guardian-status
```

The default command only prints a plan. Applying requires the explicit `x86-observe` environment marker and passes the installer admission checks. Collector runs in `guardian-observer.slice`; Runtime runs in `guardian-runtime.slice`.

The installer prints a backup path. Roll back with that exact path:

```bash
sudo ./scripts/install-guardian-x86.sh --rollback /var/backups/guardian-x86-installer/<timestamp> --environment x86-observe
```

Rollback restores installer-managed files and recorded service states. Review the generated plan and host effects before applying; local test results are not production validation.
