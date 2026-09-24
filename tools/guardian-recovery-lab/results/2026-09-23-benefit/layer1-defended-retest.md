# Layer-1 defended retest: transport defense did NOT move the boundary

2026-09-24, after deploying vm.min_free_kbytes 45056 -> 120318 (persisted)
and confirming rescue.slice memory.min=512M (per-cgroup TCP protection on
kernel 6.6+).

## same protocol, same pinned form (24x280MiB swarm, 15 OOM kills, swap 100%)

| config | r1 | r2 | aggregate | stage |
| --- | --- | --- | --- | --- |
| on, defended | 9/20 | 11/20 | 20/40 (50% fail) | transport (all) |
| on, undefended (prev night) | 10/20 | 9/20 | 19/40 | transport |
| off, undefended (prev night) | 9/20 | 10/20 | 19/40 | transport |

min_free during pressure confirmed at 120318; defense was live.

## interpretation

- The min_free_kbytes raise (2.7x) and the cgroup TCP floor changed NOTHING
  statistically (20/40 vs 19/40 vs 19/40 - within run-to-run noise).
- The elevated-floor latency is visible though: defended runs' minimum
  latency rose from ~160-290ms to 635-639ms (the reserved memory is
  protected, so even successful probes wait longer for reclaim), while the
  failure mode is unchanged.
- Conclusion: on THIS host class (WSL2, 3.8GiB, swap 100% exceeded), the
  transport failure under the pinned form is a property of the platform's
  memory/vsock architecture, not a tunable kernel-waterline problem. The
  remaining hypothesis is WSL-specific (vsock + dynamic memory balloon);
  the x86_64 native retest is now the decisive experiment for layer 1.
- The defense configuration is kept: it is harmless (latency floor rise is
  irrelevant at these scales), standard practice for production hosts, and
  the x86 retest may show real benefit there where the RAM is larger and
  the pressure margin is thinner.

## updated layer-1 verdict

"Entry capability under the pinned form: NOT GUARANTEED on the WSL2 host
class; kernel waterline tuning does not recover it. Next decisive test:
x86_64 native disposable host."
