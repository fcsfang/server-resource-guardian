# Platform-stability retest conclusion (2026-09-24)

## configurations tested (all: pinned 24x280MiB swarm form, on-build, same probe protocol)

| config | entry result | notes |
| --- | --- | --- |
| default wslconfig (4GB, gradual reclaim) | 19/40 ok | elapsed max 8.4s |
| + kernel defense (min_free 2.7x, cgroup TCP floor) | 20/40 ok | max 5.8s - no change |
| + platform change (3GB static, no balloon) | 11/40 ok | max 207s(!) - WORSE |

## verdict for the WSL host class (now definitive)

Entry capability under the pinned collapse form is NOT recoverable by any
knob available from inside the guest or the platform configuration:
- kernel waterlines (min_free_kbytes 2.7x): null effect
- per-cgroup TCP protection (memory.min): already in place, no effect
- platform elasticity (static memory, no balloon): NEGATIVE effect -
  the balloon/reclaim mechanism, normally an enemy, is what absorbs part
  of the pinned demand; removing it removes the only shock absorber.
- Guardian independence stands: on == off at every configuration.

## decision

.wslconfig reverted to the 4GB/gradual baseline (the best of the three).
Transport defense (min_free_kbytes) KEPT: harmless, standard practice,
and may matter on native hardware. All three configurations documented.
x86_64 native disposable host is the only remaining path to a layer-1
verdict on real hardware - it is a prerequisite in the ROADMAP anyway.

## environment state after experiments

Reverted baseline restored; swarm remnants cleaned (0 containers, 0 hogs,
3.2GiB available, guardian stack active). System idle and safe.
