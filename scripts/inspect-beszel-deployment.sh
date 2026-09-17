#!/usr/bin/env bash

# Read-only follow-up inventory. It deliberately avoids command lines and
# environment variables because they may contain Beszel keys or tokens.

set -u
export LC_ALL=C

section() {
  printf '\n\n===== %s =====\n' "$1"
}

run_shell() {
  printf '\n$ %s\n' "$1"
  sh -c "$1" 2>&1 || printf '[command exited with status %s]\n' "$?"
}

section "Processes with safe columns"
run_shell "ps -eo pid,user,comm,cgroup --no-headers | grep -i '[b]eszel'"

section "System services and unit files"
run_shell "systemctl list-units --all --type=service --no-pager | grep -i '[b]eszel'"
run_shell "systemctl list-unit-files --type=service --no-pager | grep -i '[b]eszel'"
run_shell "find /etc/systemd/system /lib/systemd/system /usr/lib/systemd/system -maxdepth 2 -type f -iname '*beszel*' -print 2>/dev/null"

section "Containers"
if command -v docker >/dev/null 2>&1; then
  run_shell "docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}}' | grep -i '[b]eszel'"
fi
if command -v podman >/dev/null 2>&1; then
  run_shell "podman ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}}' | grep -i '[b]eszel'"
fi

section "Known binary locations"
run_shell "find /opt /usr/local/bin /usr/bin -maxdepth 3 -type f -iname '*beszel*' -ls 2>/dev/null"

section "Packages"
if command -v dpkg-query >/dev/null 2>&1; then
  run_shell "dpkg-query -W -f='\${binary:Package} \${Version}\n' 2>/dev/null | grep -i '[b]eszel'"
fi
if command -v snap >/dev/null 2>&1; then
  run_shell "snap list 2>/dev/null | grep -i '[b]eszel'"
fi

section "Listening processes on default ports"
if command -v ss >/dev/null 2>&1; then
  run_shell "ss -lntp | awk 'NR == 1 || /:45876|:8090/'"
fi

section "Result note"
printf '\nNo output in all sections means the common Beszel deployment forms were not found on this host.\n'
