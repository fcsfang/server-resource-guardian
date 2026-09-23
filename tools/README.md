# tools/

Operator-side helpers that run outside the Guardian package's service set.

- `feishu_alert_gateway.py` + `feishu-gateway.service` - the Feishu alert
  channel: a read-only tailer of the Guardian audit stream that sends
  alert/recovery messages to a Feishu group chat. Message-only by design
  (no Docker access, no action path, no broker socket).
- `guardian-recovery-lab/` - disposable-lab probes and compact acceptance
  evidence.

## Feishu gateway install (WSL / disposable host)

```bash
install -m 755 tools/feishu_alert_gateway.py /opt/server-resource-guardian/scripts/
install -m 644 tools/feishu-gateway.service /etc/systemd/system/
# credentials: /etc/guardian/feishu.json (see config/feishu.example.json),
# 0640 root:guardian-shared, never committed
systemctl daemon-reload
systemctl enable --now feishu-gateway.service
```

The gateway is independent of `install-guardian-*.sh`; keep it installed
when the audit stream path matches (`/var/lib/guardian/runtime/audit/events.jsonl`).
It was validated end-to-end on WSL (EXP-087/088): WARNING→CRITICAL escalation,
recovery messages, stale-stream detection, and copytruncate handling all work.
