#!/usr/bin/env python3
"""Installed root-owned entry point for the bounded rescue action."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    install_root = Path(os.environ.get("GUARDIAN_INSTALL_ROOT", "/opt/server-resource-guardian"))
    sys.path.insert(0, str(install_root))
    from src.guardian_rescue_action import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
