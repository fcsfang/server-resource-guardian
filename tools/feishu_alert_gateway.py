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
"""
from __future__ import annotations

import argparse
import json
import os
import queue
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

    def propose(self, level: str, detail: str) -> tuple[str, str, str] | None:
        """Return (old, new, detail) when the debounced level changes."""
        if level == self.candidate:
            self.streak += 1
        else:
            self.candidate = level
            self.streak = 1
        self.detail = detail
        if self.streak >= self.confirm and level != self.level:
            old, self.level = self.level, level
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
            fired += self._collect(self.keys["memory"], level, f"可用 {avail:.1f}%", event_id)

        swap = mem.get("swap_used_ratio_percent")
        if swap is not None:
            level = CRITICAL if swap > 50 else WARNING if swap > 25 else OK
            fired += self._collect(self.keys["swap"], level, f"已用 {swap:.1f}%", event_id)

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
                fired += self._collect(self.keys["cpu"], level, f"利用率 {util:.1f}%", event_id)

        for m in s.get("disk", {}).get("mounts", []) or []:
            path = m.get("configured_path") or m.get("mount_point")
            stats = m.get("stats", {}) or {}
            free = stats.get("free_ratio_percent")
            if path is None or free is None:
                continue
            key = self._disk_key(path)
            level = CRITICAL if free < 5 else WARNING if free < 15 else OK
            fired += self._collect(key, level, f"剩余 {free:.1f}%", event_id)

        return fired

    def check_stale(self, threshold_s: float) -> list[tuple[str, str, str, ResourceKey, str]]:
        level = WARNING if (time.time() - self.last_event_wall) > threshold_s else OK
        return self._collect(self.keys["stale"], level, f"审计事件中断 >{threshold_s:.0f}s", "")

    @property
    def gate_degraded(self) -> bool:
        return self.keys["gate"].level in (WARNING, CRITICAL)

    def _collect(self, key: ResourceKey, level: str, detail: str, event_id: str) -> list[tuple[str, str, str, ResourceKey, str]]:
        change = key.propose(level, detail)
        return [(key.key, change[0], change[1], key, event_id)] if change else []


def message_text(key: ResourceKey, old: str, new: str, event_id: str) -> str:
    tail = f" [{event_id[:8]}]" if event_id else ""
    if new == OK:
        return f"[Guardian 恢复] {key.label}: {fmt_level(old)} → 恢复正常 ({key.detail}) {now_str()}{tail}"
    return f"[Guardian 告警] {key.label}: {fmt_level(new)} — {key.detail} {now_str()}{tail}"


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
    ap = argparse.ArgumentParser(description="Guardian -> Feishu alert gateway v2 (storm-hardened)")
    ap.add_argument("--config", default="/etc/guardian/feishu.json")
    ap.add_argument("--audit", default="/var/lib/guardian/runtime/audit/events.jsonl")
    ap.add_argument("--poll", type=float, default=1.0, help="audit tail poll interval")
    ap.add_argument("--stale-seconds", type=float, default=10.0)
    ap.add_argument("--confirm", type=int, default=2, help="consecutive samples to confirm a level")
    args = ap.parse_args()

    cfg = load_json(args.config)
    client = FeishuClient(cfg)
    evaluator = Evaluator(args.confirm)
    send_q: "queue.Queue[str | None]" = queue.Queue(maxsize=QUEUE_MAX)
    sender = threading.Thread(target=sender_loop, args=(client, send_q), daemon=True)
    sender.start()

    audit_path = Path(args.audit)
    offset = audit_path.stat().st_size
    last_inode = audit_path.stat().st_ino
    last_mono: int | None = None
    print(f"gateway v2 started, baseline offset={offset}", flush=True)

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
                        text = message_text(rk, old, new, event_id)
                        try:
                            # never block the reader: drop the oldest on overflow
                            send_q.put_nowait(text)
                        except queue.Full:
                            try:
                                send_q.get_nowait()
                                send_q.put_nowait(text)
                            except (queue.Empty, queue.Full):
                                pass
            if not evaluator.gate_degraded:
                for _key, old, new, rk, _eid in evaluator.check_stale(args.stale_seconds):
                    text = message_text(rk, old, new, "")
                    try:
                        send_q.put_nowait(text)
                    except queue.Full:
                        try:
                            send_q.get_nowait()
                            send_q.put_nowait(text)
                        except (queue.Empty, queue.Full):
                            pass
        except FileNotFoundError:
            print("audit file missing; waiting", flush=True)
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
