# Layer-1/2 acceptance progress log (night run, 2026-09-23)

20:05 task4 done: WSL on 753a454, all services active, rescue ok
20:07 layer-1 idle baseline: 40/40 pass (two runs x 20), 119-162ms
20:08 collapse run #1 started: swarm ramp
20:12 swarm up inside script (24 containers, 19 hogs alive, RAM 286Mi free)
20:14-20:26 WSL vsock channel died (0x8007274c) repeatedly - the pinned
  storm saturated the VM so hard the Windows<->WSL transport died with it
20:26 wsl --shutdown + service restart -> channel back
20:27 post-reset state: 18 hog procs, 28 containers survived the reset;
  full cleanup -> 0 containers, 3.2GiB available, guardian stack active
20:29 collapse run #2 restarted in SEGMENTS (data persists per segment,
  no cross-VM dependency inside a segment)
lesson recorded: collapse form kills not just monitoring but the host
management transport itself - this is the strongest Layer-1 evidence yet:
WITHOUT guardian's defenses the management plane becomes unreachable from
outside (though loopback probes may still work - to be verified by the
segment data)
