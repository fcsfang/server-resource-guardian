# login-path hardening notes (search-validated 4-layer stack from
# ianlpaterson.com/blog/oom-lockout-ssh-survival-hardening/ + kernel docs)

Layer 2 (admin reserve) - the article's single most direct fix:
  vm.admin_reserve_kbytes: 8192 -> 262144  (root always has login headroom)
  vm.user_reserve_kbytes:  124669 -> 131072
Layer 3 (early userspace killer): earlyoom installed & configured
  -m 10 -s 100 (RAM-floor only trigger; -s 10 is the classic misconfig)
  --avoid sshd/systemd/logind/guardian; --prefer python/stress-ng/dd
  GOTCHA found live: the unit passed $EARLYOOM_ARGS literally (unexpanded)
  -> fixed via ExecStart=/bin/sh -c 'earlyoom ${EARLYOOM_ARGS}' drop-in.
  Verified: full args visible in ps, thresholds logged correctly.
Layer 4 (thrash duration): vm.swappiness 60 -> 10 (shorten time-to-decision)
Layer 1 (oom_score shields) was already in place from the night run.

Hypothesis under test: the pinned-collapse transport failure observed on
WSL2 was SWAP-THRASH LOCKOUT (minutes of unresponsive accept path while
the kernel grinds), not resource starvation per se. earlyoom should keep
mem-available above the lockout threshold by killing hogs at 10%, and
admin_reserve gives the login fork its memory even in the worst moment.
