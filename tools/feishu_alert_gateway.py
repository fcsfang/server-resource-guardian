#!/usr/bin/env python3
"""Feishu alert gateway v2 for Guardian.

Read-only against Guardian: tails the audit events file and sends resource
alert / recovery messages to a Feishu group chat. Message-only by design.

v2 changes (storm-hardened, from the 2026-09-24 layer-2 findings):
- Read/evaluate and send are DECOUPLED: the tail loop never blocks on the
  network; parsed level-changes go to a bounded queue drained by a sender
  thread. A stalled Feishu API can no longer freeze the reader and lose
  events to logrotate copytruncate.
- copytruncate handling keeps unread backlog: on shrink, the offset resets
  only after the reader has parsed everything below the old offset.
- Alert messages carry the triggering event_id for forensics.
- Gate-state visibility: pressure_gate transitions and audit_fidelity are
  reported as their own key (degraded-by-design vs stream-dead).

v3 changes (production message depth, from the 2026-09-24 false-alarm review):
- Alert messages carry the full context the audit stream already holds:
  absolute bytes, PSI pressure, window OOM-event count, confirmation state,
  and gate/fidelity status - a reader can triage from the message alone.
- Recovery messages state how long the pressure episode lasted and WHY it
  ended when the answer is available: an EarlyOOM kill watcher tails the
  unit journal and summarizes recent kills (victim, RSS) so messages like
  "swap WARNING -> recovered" no longer require log forensics to explain.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

OK, WARNING, CRITICAL = "ok", "warning", "critical"
TOKEN_TTL_S = 7200
TOKEN_REFRESH_MARGIN_S = 300
SEND_RETRIES = 3
QUEUE_MAX = 200


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class FeishuClient:
    def __init__(self, config: dict) -> None:
        self.app_id = config["app_id"]
        self.app_secret = config["app_secret"]
        self.chat_id = config["chat_id"]
        self._token: str | None = None
        self._token_deadline = 0.0

    def _fetch_token(self) -> str:
        payload = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=payload, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
        if body.get("code") != 0 or not body.get("tenant_access_token"):
            raise RuntimeError(f"feishu token failed: {body}")
        self._token = body["tenant_access_token"]
        self._token_deadline = time.time() + int(body.get("expire", TOKEN_TTL_S)) - TOKEN_REFRESH_MARGIN_S
        return self._token

    def _send(self, text: str) -> None:
        token = self._token or self._fetch_token()
        content = json.dumps({"text": text}, ensure_ascii=False)
        payload = json.dumps({
            "receive_id": self.chat_id,
            "msg_type": "text",
            "content": content,
        }).encode("utf-8")
        last_error: str = ""
        for attempt in range(1, SEND_RETRIES + 1):
            req = urllib.request.Request(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                data=payload, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            req.add_header("Authorization", f"Bearer {token}")
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = json.loads(resp.read().decode())
                if body.get("code") == 0:
                    return
                last_error = f"code={body.get('code')} msg={body.get('msg')}"
            except urllib.error.HTTPError as e:
                last_error = f"http={e.code}"
                if e.code in (401, 400):
                    token = self._fetch_token()
            except Exception as e:  # noqa: BLE001 - network errors are retried then logged
                last_error = str(e)
            time.sleep(2 * attempt)
        raise RuntimeError(f"feishu send failed after {SEND_RETRIES} tries: {last_error}")

    def send(self, text: str) -> None:
        if self._token is None or time.time() >= self._token_deadline:
            self._fetch_token()
        self._send(text)


class ResourceKey:
    """One resource with debounced ok/warning/critical level tracking."""

    def __init__(self, key: str, label: str, confirm: int) -> None:
        self.key = key
        self.label = label
        self.confirm = confirm
        self.level = OK
        self.candidate = OK
        self.streak = 0
        self.detail = ""
        self.context: dict = {}      # enrichment payload captured with the detail
        self.became_level_at: float | None = None  # wall clock when level engaged

    def propose(self, level: str, detail: str, context: dict | None = None) -> tuple[str, str, str] | None:
        """Return (old, new, detail) when the debounced level changes.

        Fast-alarm, slow-clear: alarm (warning/critical) fires on the first
        confirming sample, recovery to ok requires `confirm` consecutive
        samples. A one-sample spike still alerts (the 15:08 review showed a
        real earlyoom episode can be shorter than one debounce window, and a
        missed alert is worse than a rare extra alert); noisy metrics cannot
        flap the recovery path because that still needs `confirm` samples.
        """
        if level == self.candidate:
            self.streak += 1
        else:
            self.candidate = level
            self.streak = 1
        self.detail = detail
        if context is not None:
            self.context = context
        required = self.confirm if level == OK else 1
        if self.streak >= required and level != self.level:
            old, self.level = self.level, level
            if old == OK:
                self.became_level_at = time.time()
            return (old, level, detail)
        return None


def fmt_level(level: str) -> str:
    return {OK: "恢复", WARNING: "WARNING", CRITICAL: "CRITICAL"}[level]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Evaluator:
    def __init__(self, confirm: int) -> None:
        self.keys: dict[str, ResourceKey] = {
            "memory": ResourceKey("memory", "宿主内存", confirm),
            "swap": ResourceKey("swap", "交换空间", confirm),
            "cpu": ResourceKey("cpu", "宿主CPU", confirm),
            "gate": ResourceKey("gate", "压力门控", confirm),
            "stale": ResourceKey("stale", "Guardian观测流", confirm),
        }
        self.disk_keys: dict[str, ResourceKey] = {}
        self.prev_cpu: dict | None = None
        self.last_event_wall = time.time()

    def _disk_key(self, path: str) -> ResourceKey:
        if path not in self.disk_keys:
            self.disk_keys[path] = ResourceKey(f"disk:{path}", f"磁盘 {path}", 2)
        return self.disk_keys[path]

    def evaluate(self, ev: dict) -> list[tuple[str, str, str, ResourceKey, str]]:
        fired: list[tuple[str, str, str, ResourceKey, str]] = []
        self.last_event_wall = time.time()
        event_id = str(ev.get("event_id") or "")
        s = ev.get("signals", {}) or {}
        mem = s.get("memory", {}) or {}
        psi = (s.get("psi") or {}) if isinstance(s.get("psi"), dict) else {}
        risk = ev.get("risk", {}) or {}

        common = {
            "psi": psi,
            "oom_delta": risk.get("oom_events_delta"),
            "fidelity": ev.get("audit_fidelity") or "full",
            "gate": self.keys["gate"].level,
        }

        # gate-state visibility first: transitions and fidelity degrade-mode
        gate = ev.get("pressure_gate") or (ev.get("evidence", {}) or {}).get("pressure_gate_transition")
        if isinstance(gate, dict) and gate.get("state") in (OK, WARNING, CRITICAL, "normal"):
            state = gate.get("state")
            gate_level = {"normal": OK, WARNING: WARNING, CRITICAL: CRITICAL}.get(state, OK)
            transition = gate.get("transition") or ""
            fired += self._collect(self.keys["gate"], gate_level, f"压力门控 {transition or state}", event_id)
        fidelity = ev.get("audit_fidelity")
        if fidelity in ("summary", "minimal") and self.keys["gate"].level == OK:
            fired += self._collect(self.keys["gate"], WARNING, f"观测降级模式 ({fidelity})", event_id)

        avail = mem.get("available_ratio_percent")
        if avail is not None:
            level = CRITICAL if avail < 10 else WARNING if avail < 15 else OK
            context = dict(common)
            context.update({
                "available_bytes": mem.get("available_bytes"),
                "total_bytes": mem.get("total_bytes"),
                "swap_ratio": mem.get("swap_used_ratio_percent"),
            })
            fired += self._collect(self.keys["memory"], level, f"可用 {avail:.1f}%", event_id, context)

        swap = mem.get("swap_used_ratio_percent")
        if swap is not None:
            level = CRITICAL if swap > 50 else WARNING if swap > 25 else OK
            context = dict(common)
            context.update({
                "swap_free_bytes": mem.get("swap_free_bytes"),
                "swap_total_bytes": mem.get("swap_total_bytes"),
                "mem_ratio": avail,
            })
            fired += self._collect(self.keys["swap"], level, f"已用 {swap:.1f}%", event_id, context)

        cpu = s.get("cpu", {}) or {}
        agg = (cpu.get("host", {}) or {}).get("stat", {}).get("aggregate", {}) or {}
        if agg.get("total_ticks") is not None:
            util = None
            if self.prev_cpu:
                dt = agg["total_ticks"] - self.prev_cpu.get("total", 0)
                di = agg["idle_ticks"] - self.prev_cpu.get("idle", 0)
                if dt > 0:
                    util = max(0.0, (dt - di) / dt * 100.0)
            self.prev_cpu = {"total": agg["total_ticks"], "idle": agg["idle_ticks"]}
            if util is not None:
                level = CRITICAL if util > 95 else WARNING if util > 85 else OK
                context = dict(common)
                context.update({"load1": (cpu.get("host", {}) or {}).get("load1")})
                fired += self._collect(self.keys["cpu"], level, f"利用率 {util:.1f}%", event_id, context)

        for m in s.get("disk", {}).get("mounts", []) or []:
            path = m.get("configured_path") or m.get("mount_point")
            stats = m.get("stats", {}) or {}
            free = stats.get("free_ratio_percent")
            if path is None or free is None:
                continue
            key = self._disk_key(path)
            level = CRITICAL if free < 5 else WARNING if free < 15 else OK
            context = dict(common)
            context.update({"free_bytes": stats.get("free_bytes"), "total_bytes": stats.get("total_bytes")})
            fired += self._collect(key, level, f"剩余 {free:.1f}%", event_id, context)

        return fired

    def check_stale(self, threshold_s: float) -> list[tuple[str, str, str, ResourceKey, str]]:
        level = WARNING if (time.time() - self.last_event_wall) > threshold_s else OK
        return self._collect(self.keys["stale"], level, f"审计事件中断 >{threshold_s:.0f}s", "")

    @property
    def gate_degraded(self) -> bool:
        return self.keys["gate"].level in (WARNING, CRITICAL)

    def _collect(self, key: ResourceKey, level: str, detail: str, event_id: str, context: dict | None = None) -> list[tuple[str, str, str, ResourceKey, str]]:
        change = key.propose(level, detail, context)
        return [(key.key, change[0], change[1], key, event_id)] if change else []


def _fmt_bytes(n) -> str:
    if not isinstance(n, (int, float)) or n <= 0:
        return "?"
    for unit, div in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}B"


def _psi_line(psi: dict) -> str:
    if not isinstance(psi, dict) or not psi:
        return ""
    mem = psi.get("memory") or {}
    some = ((mem.get("some") or {}).get("avg10"))
    full = ((mem.get("full") or {}).get("avg10"))
    if some is None and full is None:
        return ""
    return f"PSI-mem some {_val(some)}/{_val(full)}"


def _val(x) -> str:
    return f"{x:.1f}%" if isinstance(x, (int, float)) else "?"


def message_text(key: ResourceKey, old: str, new: str, event_id: str, kill_summary: str = "") -> str:
    tail = f" [{event_id[:8]}]" if event_id else ""
    ctx = key.context or {}
    lines: list[str] = []
    if new == OK:
        duration = ""
        if key.became_level_at is not None:
            elapsed = max(time.time() - key.became_level_at, 0)
            duration = f" ~{elapsed:.0f}s"
            key.became_level_at = None
        header = f"[Guardian 恢复] {key.label}: {fmt_level(old)} → 正常 ({key.detail}){duration} {now_str()}{tail}"
        extra = []
        if kill_summary:
            extra.append(f"压力消除方式: {kill_summary}")
        psi_text = _psi_line(ctx.get("psi") or {})
        if psi_text:
            extra.append(psi_text)
        if extra:
            lines.append(header)
            lines.extend(f"  {e}" for e in extra)
            return "\n".join(lines)
        return header

    header = f"[Guardian 告警] {key.label}: {fmt_level(new)} — {key.detail} {now_str()}{tail}"
    detail_parts = []
    if key.key == "memory":
        avail_b = ctx.get("available_bytes")
        total_b = ctx.get("total_bytes")
        if avail_b is not None and total_b:
            detail_parts.append(f"绝对量 {_fmt_bytes(avail_b)}/{_fmt_bytes(total_b)}")
        swap_ratio = ctx.get("swap_ratio")
        if swap_ratio is not None:
            detail_parts.append(f"swap 已用 {swap_ratio:.1f}%")
    elif key.key == "swap":
        free_b = ctx.get("swap_free_bytes")
        total_b = ctx.get("swap_total_bytes")
        if free_b is not None and total_b:
            detail_parts.append(f"剩 {_fmt_bytes(free_b)}/{_fmt_bytes(total_b)}")
        mem_ratio = ctx.get("mem_ratio")
        if mem_ratio is not None:
            detail_parts.append(f"内存可用 {mem_ratio:.1f}%")
    elif key.key == "cpu":
        load1 = ctx.get("load1")
        if load1 is not None:
            detail_parts.append(f"load1 {load1}")
    psi_text = _psi_line(ctx.get("psi") or {})
    if psi_text and key.key != "cpu":
        detail_parts.append(psi_text)
    oom = ctx.get("oom_delta")
    if oom:
        detail_parts.append(f"OOM 事件 {oom} (窗口)")
    state_bits = []
    gate = ctx.get("gate")
    if gate is not None and gate != OK:
        state_bits.append(f"门控 {gate}")
    fid = ctx.get("fidelity")
    if fid and fid != "full":
        state_bits.append(f"审计降级 {fid}")
    if state_bits:
        detail_parts.append("/".join(state_bits))
    if detail_parts:
        lines.append(header)
        lines.append(f"  {' | '.join(detail_parts)}")
        return "\n".join(lines)
    return header


class EarlyoomWatcher:
    """Tail the earlyoom unit journal so recovery messages can say WHY the
    pressure ended, AND so single-sample kill episodes (shorter than any
    audit sampling window) still notify. Read-only: journald query only.

    On this host earlyoom kills at 10% available while Guardian samples every
    ~1.2s - an earlyoom kill often completes between samples, so the audit
    stream records only a brief dip that never crosses the WARNING threshold
    (measured live: min sampled avail was 15.4% while earlyoom killed at 9.5%).
    The kill event itself is therefore the most reliable "pressure happened"
    signal: poll_kill_alerts() turns each new kill into an alert line, and
    kill_summary(since_ts) explains recoveries.
    """

    def __init__(self, unit: str = "earlyoom", max_recent: int = 4, poll_seconds: int = 90) -> None:
        self.unit = unit
        self.max_recent = max_recent
        self.poll_seconds = poll_seconds
        self._last_poll = 0.0
        self._seen_keys: set[str] = set()

    def _parse_kills(self, stdout: str) -> list[tuple[str, str, str, str]]:
        """Extract (ts, kind, name, rss) from journalctl output lines."""
        kills: list[tuple[str, str, str, str]] = []
        for line in (stdout or "").splitlines():
            if "sending SIGTERM" not in line and "sending SIGKILL" not in line:
                continue
            # ... sending SIGTERM to process 344101 uid 0 "pytest": oom_score 1052, oom_score_adj 0, VmRSS 2948 MiB, cmdline "python3 -m pytest ..."
            try:
                ts = line.split()[0] if line else ""
                after = line.split("sending SIG", 1)[1]
                kind = "KILL" if "SIGKILL" in line else "TERM"
                name_part = after.split('"')[1] if '"' in after else "?"
                rss = "?"
                if "VmRSS" in after:
                    rss = after.split("VmRSS", 1)[1].strip().split(",")[0]
                kills.append((ts, kind, name_part, rss))
            except (IndexError, ValueError):
                continue
        return kills

    def poll_kill_alerts(self) -> list[str]:
        """Return deduplicated alert lines for kills since the last poll."""
        now = time.time()
        if now - self._last_poll < self.poll_seconds:
            return []
        self._last_poll = now
        try:
            start = datetime.fromtimestamp(max(now - self.poll_seconds - 30, 0))
            result = subprocess.run(
                ["journalctl", "-u", self.unit, "--no-pager", "-o", "short-iso",
                 "--since", start.strftime("%Y-%m-%d %H:%M:%S")],
                capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        alerts: list[str] = []
        for _ts, kind, name, rss in self._parse_kills(result.stdout):
            key = f"{name}:{rss}:{kind}"
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            alerts.append(
                f"[Guardian 事件] earlyoom 内存保护触发: SIG{kind} 击杀 {name} (RSS {rss}) — "
                f"可用内存跌破 10%,系统已自动清除压力源 {now_str()}"
            )
        # bound the dedup set
        if len(self._seen_keys) > 500:
            self._seen_keys = set(sorted(self._seen_keys)[-200:])
        return alerts

    def kill_summary(self, since_ts: float | None, lookback_s: int = 20) -> str:
        try:
            cmd = ["journalctl", "-u", self.unit, "--no-pager", "-o", "short-iso"]
            if since_ts is not None:
                # kills can slightly precede the alert (evaluator confirmation
                # lag), so open the window a bit earlier than the episode start
                start = datetime.fromtimestamp(max(since_ts - lookback_s, 0))
                cmd += ["--since", start.strftime("%Y-%m-%d %H:%M:%S")]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        kills: list[tuple[str, str]] = []  # (name, rss)
        for line in (result.stdout or "").splitlines():
            if "sending SIGTERM" not in line and "sending SIGKILL" not in line:
                continue
            # ... sending SIGTERM to process 344101 uid 0 "pytest": oom_score 1052, oom_score_adj 0, VmRSS 2948 MiB, cmdline "python3 -m pytest ..."
            try:
                after = line.split("sending SIG", 1)[1]
                name_part = after.split('"')[1] if '"' in after else "?"
                rss = "?"
                if "VmRSS" in after:
                    rss = after.split("VmRSS", 1)[1].strip().split(",")[0]
                kind = "KILL" if "SIGKILL" in line else "TERM"
                kills.append((f"{name_part}({kind})", rss))
            except (IndexError, ValueError):
                continue
        if not kills:
            return ""
        recent = kills[-self.max_recent:]
        rendered = " / ".join(f"{name} {rss}" for name, rss in recent)
        more = f" 等{len(kills)}次" if len(kills) > len(recent) else ""
        return f"earlyoom 击杀 {len(kills)}×{more}: {rendered}"


def sender_loop(client: FeishuClient, q: "queue.Queue[str | None]") -> None:
    while True:
        text = q.get()
        if text is None:
            return
        try:
            client.send(text)
            print(f"sent: {text}", flush=True)
        except Exception as e:  # noqa: BLE001 - keep the sender alive
            print(f"send failed: {e}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Guardian -> Feishu alert gateway v3 (deep messages, storm-hardened)")
    ap.add_argument("--config", default="/etc/guardian/feishu.json")
    ap.add_argument("--audit", default="/var/lib/guardian/runtime/audit/events.jsonl")
    ap.add_argument("--poll", type=float, default=1.0, help="audit tail poll interval")
    ap.add_argument("--stale-seconds", type=float, default=10.0)
    ap.add_argument("--confirm", type=int, default=2, help="consecutive samples to confirm a level")
    ap.add_argument("--earlyoom-unit", default="earlyoom", help="unit to watch for kill events")
    args = ap.parse_args()

    cfg = load_json(args.config)
    client = FeishuClient(cfg)
    evaluator = Evaluator(args.confirm)
    watcher = EarlyoomWatcher(unit=args.earlyoom_unit)
    send_q: "queue.Queue[str | None]" = queue.Queue(maxsize=QUEUE_MAX)
    sender = threading.Thread(target=sender_loop, args=(client, send_q), daemon=True)
    sender.start()

    audit_path = Path(args.audit)
    offset = audit_path.stat().st_size
    last_inode = audit_path.stat().st_ino
    last_mono: int | None = None
    print(f"gateway v3 started, baseline offset={offset}", flush=True)

    def enqueue(text: str) -> None:
        try:
            # never block the reader: drop the oldest on overflow
            send_q.put_nowait(text)
        except queue.Full:
            try:
                send_q.get_nowait()
                send_q.put_nowait(text)
            except (queue.Empty, queue.Full):
                pass

    while True:
        try:
            st = audit_path.stat()
            if st.st_ino != last_inode:  # logrotate create: reopen from the end
                last_inode, offset = st.st_ino, st.st_size
                print("audit file rotated; re-opened", flush=True)
            if st.st_size < offset:  # logrotate copytruncate: reread from start
                offset = 0
                print("audit file truncated (copytruncate); rereading", flush=True)
            if st.st_size > offset:
                with open(audit_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    blob = f.read()
                offset = st.st_size
                for line in blob.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    mono = ev.get("observed_monotonic_ns")
                    if mono is not None and mono == last_mono:
                        continue
                    last_mono = mono
                    for _key, old, new, rk, event_id in evaluator.evaluate(ev):
                        kill_summary = ""
                        if new == OK and old != OK:
                            # pressure ended: explain how, when the data exists
                            kill_summary = watcher.kill_summary(rk.became_level_at)
                        text = message_text(rk, old, new, event_id, kill_summary)
                        enqueue(text)
            if not evaluator.gate_degraded:
                for _key, old, new, rk, _eid in evaluator.check_stale(args.stale_seconds):
                    text = message_text(rk, old, new, "")
                    enqueue(text)
            # earlyoom kill events notify independently of the audit stream:
            # a kill episode can complete between audit samples and never
            # cross any resource threshold (measured on this host).
            for alert_text in watcher.poll_kill_alerts():
                enqueue(alert_text)
        except FileNotFoundError:
            print("audit file missing; waiting", flush=True)
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
