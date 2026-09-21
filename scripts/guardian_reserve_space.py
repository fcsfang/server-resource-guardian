#!/usr/bin/env python3
"""Manage Guardian's own pre-created emergency disk reserve.

The command intentionally has no arbitrary delete path.  ``release`` can only
remove the exact file described by a matching manifest created by this tool.
It never removes logs, Docker data, business files, or an operator-supplied
path.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path


DEFAULT_ROOT = Path("/var/lib/guardian/reserve")
DEFAULT_SIZE = 256 * 1024 * 1024
MAX_SIZE = 1024 * 1024 * 1024
RESERVE_NAME = "emergency-space.bin"
MANIFEST_NAME = "manifest.json"
CREATED_BY = "server-resource-guardian/maintenance-reserve-v1"


def _paths(root: Path) -> tuple[Path, Path]:
    return root / RESERVE_NAME, root / MANIFEST_NAME


def _manifest(path: Path, size: int) -> dict[str, object]:
    return {
        "schema": "guardian.emergency_reserve.v1",
        "created_by": CREATED_BY,
        "path": str(path),
        "size_bytes": size,
        "mode": "release-only-own-file",
    }


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid Guardian reserve manifest: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit("Guardian reserve manifest is not an object")
    return value


def create(root: Path, size: int) -> dict[str, object]:
    if size <= 0 or size > MAX_SIZE:
        raise SystemExit(f"size must be between 1 and {MAX_SIZE} bytes")
    reserve, manifest_path = _paths(root)
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    if reserve.exists() or manifest_path.exists():
        if not reserve.is_file() or not manifest_path.is_file():
            raise SystemExit("Guardian reserve path is occupied by an unexpected object")
        existing = _read_manifest(manifest_path)
        if existing == _manifest(reserve, size) and reserve.stat().st_size == size:
            return {"status": "already-present", **existing}
        raise SystemExit("existing reserve does not match Guardian's manifest; refusing overwrite")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{RESERVE_NAME}.", dir=root)
    temporary = Path(temporary_name)
    try:
        try:
            os.posix_fallocate(fd, 0, size)
        except AttributeError:
            os.ftruncate(fd, size)
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        os.close(fd)
        fd = -1
        temporary.replace(reserve)
        manifest_path.write_text(
            json.dumps(_manifest(reserve, size), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(manifest_path, 0o640)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)
    return {"status": "created", **_manifest(reserve, size)}


def release(root: Path) -> dict[str, object]:
    reserve, manifest_path = _paths(root)
    if not manifest_path.exists() and not reserve.exists():
        return {"status": "absent", "path": str(reserve)}
    if not manifest_path.is_file() or not reserve.is_file():
        raise SystemExit("Guardian reserve is incomplete; refusing any deletion")
    manifest = _read_manifest(manifest_path)
    expected = _manifest(reserve, int(manifest.get("size_bytes", -1)))
    if manifest != expected or reserve.stat().st_size != int(manifest["size_bytes"]):
        raise SystemExit("Guardian reserve manifest mismatch; refusing any deletion")
    before_stats = os.statvfs(root)
    before_free_bytes = int(before_stats.f_bavail * before_stats.f_frsize)
    reserve_deleted = False
    try:
        reserve.unlink()
        reserve_deleted = True
        manifest_path.unlink()
        after_stats = os.statvfs(root)
        after_free_bytes = int(after_stats.f_bavail * after_stats.f_frsize)
    except OSError as exc:
        if reserve_deleted:
            return {
                "status": "executed_unverified",
                "path": str(reserve),
                "size_bytes": expected["size_bytes"],
                "before_free_bytes": before_free_bytes,
                "after_free_bytes": None,
                "manifest_cleanup": "failed",
                "error": type(exc).__name__,
            }
        raise SystemExit(f"Guardian reserve release failed before deletion: {exc}") from exc
    return {
        "status": "released" if after_free_bytes > before_free_bytes else "executed_unverified",
        "path": str(reserve),
        "size_bytes": expected["size_bytes"],
        "before_free_bytes": before_free_bytes,
        "after_free_bytes": after_free_bytes,
        "manifest_cleanup": "complete",
    }


def status(root: Path) -> dict[str, object]:
    reserve, manifest_path = _paths(root)
    if not manifest_path.exists() and not reserve.exists():
        return {"status": "absent", "path": str(reserve)}
    if not manifest_path.is_file() or not reserve.is_file():
        return {"status": "incomplete", "path": str(reserve)}
    manifest = _read_manifest(manifest_path)
    return {
        "status": "ready" if reserve.stat().st_size == manifest.get("size_bytes") else "mismatch",
        **manifest,
        "actual_size_bytes": reserve.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Guardian's own emergency disk reserve")
    parser.add_argument("action", choices=("create", "release", "status"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--size-bytes", type=int, default=DEFAULT_SIZE)
    args = parser.parse_args()
    if args.action == "create":
        value = create(args.root, args.size_bytes)
    elif args.action == "release":
        value = release(args.root)
    else:
        value = status(args.root)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
