# Layer-1 SOLVED: userspace early-killer keeps the door open through the storm

2026-09-24, after deploying the search-validated 4-layer login-path hardening
(admin_reserve 256MB, swappiness 10, earlyoom with RAM-floor trigger and
process steering; oom_score shields already in place from the night run).

## the decisive test (identical pinned form: 24x280MiB swarm, guardian on)

| config | r1 | r2 | aggregate | max latency | notes |
| --- | --- | --- | --- | --- | --- |
| baseline (no defense) | 10/20 | 9/20 | 19/40 (52.5% fail) | 8.4s | swap-grind lockout |
| + kernel defense only | 9/20 | 11/20 | 20/40 | 5.8s | no change |
| + platform change only | 8/20 | 3/20 | 11/40 | 207s | worse (reverted) |
| **+ 4-layer hardening (earlyoom active)** | **20/20** | **20/20** | **40/40 (100%)** | **0.6s** | **28 earlyoom kills; window survived** |

earlyoom killed 28 hog processes during the window (mem-avail floor 10%),
the login path never entered the swap-grind lockout, and both probe runs
passed at near-baseline latency (127-599ms vs idle 119-162ms).

## root cause, finally named

The transport-stage failure under the pinned form was SWAP-THRASH LOCKOUT:
the kernel grinds swap for tens of seconds-to-minutes before the OOM killer
decides, and during that grind the TCP accept path (and the WSL vsock plane)
is unresponsive. Kernel-side tuning cannot prevent the grind because it fires
only at true exhaustion; platform knobs change how much grinding happens.
A userspace killer that acts at 10% available removes the grind entirely.

## why the earlier conclusions were right and incomplete

- "guardian-independent (on == off)": TRUE - the lockout hits any
  configuration equally.
- "not a tunable kernel-waterline problem": TRUE for waterlines; the fix was
  never a waterline - it is a CHANGE OF WHO KILLS FIRST (userspace, early,
  PSI-informed).
- "WSL platform property": PARTIALLY TRUE - the vsock plane amplifies the
  grind (0x8007274c channel deaths), but the grind itself is generic Linux
  swap behavior, and it is fixable. The x86 retest remains valuable but the
  mechanism is now understood, not merely attributed.

## verdict

Layer-1 entry capability under the pinned collapse form: SOLVED on this host
class (40/40, two runs, pass line >=95% exceeded). The hardening stack is
committed as deployable configuration. Remaining caveats: (a) earlyoom's
SIGTERM of hogs is itself an action on host processes - under Guardian's
safety rules earlyoom must be considered a platform-level safety net, not a
Guardian action (it never touches Guardian-managed business containers by
steering config, but the boundary must be documented); (b) x86 native retest
still pending for the final cross-platform confirmation.

## follow-ups

1. Commit the hardening as deployable templates (sysctl + earlyoom config).
2. Document the earlyoom-vs-Guardian action boundary in deploy README.
3. Layer-2 rerun tonight (unchanged plan) - the storm window now stays
   reachable, which also de-risks the observer-effect measurement.
