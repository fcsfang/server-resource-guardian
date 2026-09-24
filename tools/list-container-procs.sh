#!/bin/bash
# List the main process names of every running container, deduplicated.
# Output format: one line per container: "name: proc1 proc2 ..."
# Purpose: build the earlyoom --avoid list for real business containers.
for c in $(docker ps --format '{{.Names}}'); do
  procs=$(docker top "$c" -o pid,comm | tail -n +2 | tr -s ' ' '\n' | tail -n +2 | grep -v '^$' | sort -u | tr '\n' ' ')
  echo "$c: $procs"
done
