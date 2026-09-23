# Local Disposable Install

This package is for an Ubuntu disposable host. The installer defaults to a plan; applying requires `--apply --environment local-disposable` and leaves automatic actions disabled.

```bash
bash scripts/install-guardian-local.sh
sudo bash scripts/install-guardian-local.sh --apply --environment local-disposable
sudo guardian-status
```

The local Runtime runs in `rescue.slice`; the read-only Collector runs in `guardian-observer.slice`. `workload.slice` is only for bounded test workloads. These cgroup settings improve scheduling/reclaim priority; they do not guarantee SSH availability under global OOM, kernel, network, or disk failure.

The installer backs up files it replaces under `/var/backups/guardian-local-installer`. It does not provide an automatic rollback command. Do not apply it to a production host.
