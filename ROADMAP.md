# Guardian Acceptance Roadmap

## Product outcomes

Guardian is evaluated on exactly three outcomes:

1. Timely and correct CPU, memory, and disk alerts.
2. A usable external SSH, diagnosis, and manual-stop path at a defined pressure boundary.
3. Within an explicit authorization boundary, assist the administrator through a complete mitigation loop against the pressure source: identify affected targets by dynamic identity (labels and ownership, not a static allowlist), act on one or more of them with verified escalation (signal, wait, confirm effect, escalate only within the granted boundary), and confirm recovery separately on the host and on business health. Every step is audited and attributable; nothing runs automatically without authorization.

Native Linux controls own accounting, isolation, reclaim, and enforcement. Beszel owns monitoring and alert delivery. Guardian owns interpretation, target eligibility, authorization, one bounded request, audit, and verification.

## Experiment decision gate

Before a pressure run, record its primary product outcome, success metric, failure boundary, and the product decision it can change. Kernel, cgroup, or OOM observations alone are supporting evidence. Stop a run when it cannot reach the declared product boundary; do not add workers merely to produce a more dramatic failure.

## Operator-access acceptance contract

Outcome 2 is measured as two separated layers, because they fail in different ways and need different defenses. Both layers are evaluated under a declared collapse pressure form (the pinned-OOM churn model in `tools/collapse-pressure-model/`); a form that only produces periodic self-rescue does not exercise this contract.

**Layer 1 — entry capability (can the operator get in at all?).**
The external probe opens a fresh SSH connection for every sample and records transport, authentication, PAM/logind, shell startup, and first diagnostic command as separate stages, with the failing stage identified on any failure.

- Metric: entry success rate over at least 20 probes per run, two independent runs per configuration.
- Pass line: entry rate >= 95% in every run, no two consecutive entry failures, and no failure in the same stage across both runs (a stage that fails repeatedly is a defect, not noise).
- Guardian-on must not lower the entry rate compared with Guardian-off on the same host, pressure form, and cadence.
- Every entry failure must name its stage; defenses (rescue.slice isolation, OOMScoreAdjust, journald/logind placement) are added to eliminate the failing stage, and the run is repeated after each defense.

**Layer 2 — in-session task time (once in, how fast is the job done?).**
The operator task is: locate the pressure source, stop it gently, verify host recovery. Guardian contributes a notification lead (alert delivered while the operator walks to the terminal), a one-shot locate command (`guardian-rescue top`), and an observation budget that must not compete with the operator.

- Metric: MTTA - pressure start to task completion, split into notification lead time (MTTD) and in-session execution time (MTTI); plus per-stage latency of the ops chain.
- Pass line: Guardian-on MTTA <= 50% of Guardian-off MTTA on the same form, with Guardian-on MTTD <= 10s and MTTI P95 <= 5s; the observer effect is bounded when the pressure gate is active - docker-side operator steps at most 1.5x the Guardian-off baseline.
- A Guardian-on result may not claim an advantage from a run where the notification channel was down; notification-channel liveness under collapse (>= 1 status message within 60s of entering a degraded state) is itself a pass condition.

**Reporting rule.** Both layers are always reported together: an entry-rate-only claim hides the task-time problem; a task-time-only claim presumes entry. If Layer 1 cannot be made to fail on a given host class, report entry as "no boundary found on this class" and evaluate Layer 2 time-to-completion as the benefit metric instead - do not weaken the pressure form to manufacture a Layer 1 failure.

The historical 2-second operator SLO is superseded by the two-layer metrics above.

## Environment contract

The SSH comparison requires a disposable cgroup-v2/systemd host, an external probe client, a working out-of-band console, fixed host sizing, fixed workload images, and saved effective cgroup values. The current 2-vCPU/2-GiB `guardian-t11-lite` VM is suitable for functional and safety checks, but its completed boundary search did not make external SSH fail repeatably. No additional pressure tuning is scheduled on that VM.

A future benefit comparison should use a representative disposable x86_64 non-production host or VM with the same kernel/control configuration intended for deployment. No production connection or credential access is authorized.

## Current closeout sequence

1. **Repository reduction — complete:** retain supported product code, deployment assets, tests, compact lab tools, and summary evidence; keep raw probe streams out of the repository. Links, imports, shell syntax, compilation, and the full test suite pass.
2. **Docker business-object loop — complete locally:** one ARM64 web image was transferred offline into `guardian-t11-lite`; two disposable replicas ran in `workload.slice`; one exact full container ID received one authorized TERM; audit, host mitigation, target exit, and surviving-replica HTTP health were verified separately; observe-only configuration and fixture cleanup were restored.
3. **Operator-access two-layer acceptance:** under the pinned-collapse model, measure Layer 1 (entry rate, stage-attributed failures) and Layer 2 (MTTA split into notification lead and in-session execution) for Guardian-off vs -on; add entry defenses (OOMScoreAdjust for sshd/Guardian units) where a stage fails; close outcome 2 with both layers reported, or keep it open with the exact missing prerequisite named.

## Exit criteria

The current closeout is complete when the repository is clean and reproducibly tested, the disposable Docker loop has an auditable outcome without forced escalation, and the SSH claim is either supported by the contract above or explicitly closed as unproven. Local evidence never establishes production readiness.
