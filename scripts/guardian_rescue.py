#!/usr/bin/env python3
"""Installed entry point for Docker-independent rescue diagnostics."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    install_root = Path(os.environ.get("GUARDIAN_INSTALL_ROOT", "/opt/server-resource-guardian"))
    sys.path.insert(0, str(install_root))
    from src.guardian_rescue_cli import main as run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
