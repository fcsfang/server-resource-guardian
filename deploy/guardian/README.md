# Local Disposable Install

This package is for an Ubuntu disposable host. The installer defaults to a plan; applying requires `--apply --environment local-disposable` and leaves automatic actions disabled.

```bash
bash scripts/install-guardian-local.sh
sudo bash scripts/install-guardian-local.sh --apply --environment local-disposable
sudo guardian-status
```

The local Runtime runs in `rescue.slice`; the read-only Collector runs in `guardian-observer.slice`. `workload.slice` is only for bounded test workloads. These cgroup settings improve scheduling/reclaim priority; they do not guarantee SSH availability under global OOM, kernel, network, or disk failure.

The installer backs up files it replaces under `/var/backups/guardian-local-installer`. It does not provide an automatic rollback command. Do not apply it to a production host.

## Gateway v3 message depth + journal access requirement

The Feishu gateway (v3) enriches alert/recovery messages with the context the
audit stream already carries (absolute bytes, PSI, window OOM count, gate and
fidelity state), and explains recoveries via an EarlyOOM kill watcher
(victim name + RSS). It also emits a standalone kill alert whenever earlyoom
removes a pressure source - including episodes so short that Guardian's own
sampling never crosses a resource threshold (measured: earlyoom kills at 10%
available while runtime samples every ~1.2s, so the dip can be missed).

Requirement: the gateway service user (default `guardian`) must be able to
read the system journal for `earlyoom` kill lines. Grant it with:

```bash
sudo usermod --append --groups systemd-journal guardian
sudo systemctl restart feishu-gateway
```

Without this membership the watcher silently returns empty summaries (verified
2026-09-24); the resource-level alerts keep working either way.
