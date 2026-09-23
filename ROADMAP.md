# Guardian Acceptance Roadmap

## Product outcomes

Guardian is evaluated on exactly three outcomes:

1. Timely and correct CPU, memory, and disk alerts.
2. A usable external SSH, diagnosis, and manual-stop path at a defined pressure boundary.
3. One explicitly authorized TERM-only stop of an allowlisted target, with audit and separate host/business recovery results.

Native Linux controls own accounting, isolation, reclaim, and enforcement. Beszel owns monitoring and alert delivery. Guardian owns interpretation, target eligibility, authorization, one bounded request, audit, and verification.

## Experiment decision gate

Before a pressure run, record its primary product outcome, success metric, failure boundary, and the product decision it can change. Kernel, cgroup, or OOM observations alone are supporting evidence. Stop a run when it cannot reach the declared product boundary; do not add workers merely to produce a more dramatic failure.

## Operator-access acceptance contract

The external probe opens a fresh SSH connection for every sample and records transport, authentication, shell startup, diagnosis, `guardian-rescue status`, and `guardian-rescue top` separately.

A Guardian-off failure boundary is repeatable only when two independent runs use identical host state, pressure, cadence, and timeout, each collect at least 20 probes, and each has at least 20% complete-probe failures or timeouts with the failing stage identified. One isolated timeout is calibration noise, not a boundary.

A matched Guardian-on result passes only when the same boundary and probe sequence are used in two independent runs, at least 95% of complete probes succeed in each run, no two consecutive probes fail, and successful complete probes have P95 latency no greater than the two-second operator SLO.

If a representative disposable environment cannot reproduce the off boundary, stop the route and report that no in-band SSH advantage was established. Do not weaken the boundary to manufacture a pass.

## Environment contract

The SSH comparison requires a disposable cgroup-v2/systemd host, an external probe client, a working out-of-band console, fixed host sizing, fixed workload images, and saved effective cgroup values. The current 2-vCPU/2-GiB `guardian-t11-lite` VM is suitable for functional and safety checks, but its completed boundary search did not make external SSH fail repeatably. No additional pressure tuning is scheduled on that VM.

A future benefit comparison should use a representative disposable x86_64 non-production host or VM with the same kernel/control configuration intended for deployment. No production connection or credential access is authorized.

## Current closeout sequence

1. **Repository reduction — complete:** retain supported product code, deployment assets, tests, compact lab tools, and summary evidence; keep raw probe streams out of the repository. Links, imports, shell syntax, compilation, and the full test suite pass.
2. **Docker business-object loop — complete locally:** one ARM64 web image was transferred offline into `guardian-t11-lite`; two disposable replicas ran in `workload.slice`; one exact full container ID received one authorized TERM; audit, host mitigation, target exit, and surviving-replica HTTP health were verified separately; observe-only configuration and fixture cleanup were restored.
3. **SSH benefit boundary:** keep the local result as an explicit inconclusive boundary. Resume only when the representative disposable x86_64 prerequisite is available; otherwise close with no rescue-advantage claim and require out-of-band recovery.

## Exit criteria

The current closeout is complete when the repository is clean and reproducibly tested, the disposable Docker loop has an auditable outcome without forced escalation, and the SSH claim is either supported by the contract above or explicitly closed as unproven. Local evidence never establishes production readiness.
