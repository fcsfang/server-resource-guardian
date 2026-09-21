"""Small systemd notify/readiness adapter with a no-systemd no-op fallback."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Callable


SocketFactory = Callable[..., socket.socket]


def send_systemd_notification(
    message: str,
    *,
    notify_socket: str | None = None,
    socket_factory: SocketFactory = socket.socket,
) -> bool:
    """Send one datagram to systemd; return False when notify is unavailable."""

    address = notify_socket if notify_socket is not None else os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):  # Linux abstract AF_UNIX namespace.
        address = "\0" + address[1:]
    try:
        with socket_factory(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.connect(address)
            channel.sendall(message.encode("utf-8"))
    except (OSError, ValueError):
        return False
    return True


def systemd_notification_status(sent: bool, *, notify_socket: str | None = None) -> str:
    """Classify a systemd notification without treating no-systemd as failure."""

    configured_socket = notify_socket if notify_socket is not None else os.environ.get("NOTIFY_SOCKET")
    if not configured_socket:
        return "not_configured"
    return "sent" if sent else "failed"


def _write_readiness(value: str, readiness_file: str | None = None) -> bool:
    path_value = readiness_file if readiness_file is not None else os.environ.get("GUARDIAN_READY_FILE")
    if not path_value:
        return False
    path = Path(path_value)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(value + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return False
    return True


def write_readiness(status: str = "starting") -> bool:
    """Write the local readiness marker without claiming systemd READY=1."""

    return _write_readiness(status)


def notify_status(status: str) -> bool:
    """Publish a status transition while preserving readiness semantics."""

    file_written = write_readiness(status)
    notified = send_systemd_notification(f"STATUS={status}")
    return file_written or notified


def notify_ready(status: str = "ready") -> bool:
    """Mark the process ready and notify systemd when running under systemd."""

    file_written = write_readiness(status)
    notified = send_systemd_notification(f"READY=1\nSTATUS={status}")
    return file_written or notified


def notify_watchdog(status: str = "sampling") -> bool:
    """Refresh the systemd watchdog and readiness status if configured."""

    return send_systemd_notification(f"WATCHDOG=1\nSTATUS={status}")


__all__ = [
    "notify_ready",
    "notify_status",
    "notify_watchdog",
    "send_systemd_notification",
    "systemd_notification_status",
    "write_readiness",
]
