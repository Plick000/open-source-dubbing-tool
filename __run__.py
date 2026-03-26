#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
run_worker.py
- Detects current machine name (PC name / hostname)
- Sets WORKER_PC=<detected>
- Runs: python _pipeline_orchestrator__.py --loop   (by default)

Usage:
  python run_worker.py
  python run_worker.py --pc PC1
  python run_worker.py --once
  python run_worker.py --print-only
  python run_worker.py -- --pending /mnt/z/... --processing /mnt/z/... --poll-seconds 2
"""

from __future__ import annotations
try:
    # Works when run as a package module (python -m ...)
    from ._version__ import __version__
except ImportError:
    # Works when run as a script (python run_worker.py)
    from _version__ import __version__

from datetime import datetime

import argparse
import os
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path


_INVALID_WIN_CHARS = r'<>:"/\\|?*'
_invalid_re = re.compile(f"[{re.escape(_INVALID_WIN_CHARS)}]")
__version__: str = __version__


def sanitize_filename(name: str, replacement: str = "_") -> str:
    s = str(name or "").strip()
    s = _invalid_re.sub(replacement, s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ").strip()
    return s or "PC_UNKNOWN"


def detect_pc_name() -> str:
    # If user already set WORKER_PC externally, respect it
    env_pc = (os.getenv("WORKER_PC") or "").strip()
    if env_pc:
        return sanitize_filename(env_pc)

    # Common sources (WSL/Linux/Windows envs)
    name = (
        platform.node()
        or os.getenv("COMPUTERNAME")
        or os.getenv("HOSTNAME")
        or ""
    ).strip()

    return sanitize_filename(name) if name else "PC_UNKNOWN"


def main() -> int:
    here = Path(__file__).resolve().parent
    default_orch = here / "_pipeline_orchestrator__.py"

    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--pc", default="", help="Override detected PC name (e.g., PC1)")
    ap.add_argument("--orchestrator", default=str(default_orch), help="Path to _pipeline_orchestrator__.py")
    ap.add_argument("--once", action="store_true", help="Run one cycle (no --loop)")
    ap.add_argument("--print-only", action="store_true", help="Only print the command; do not execute")
    ap.add_argument(
        "orch_args",
        nargs=argparse.REMAINDER,
        help="Extra args for orchestrator. Use: -- <args> (recommended)",
    )

    args = ap.parse_args()

    pc_name = sanitize_filename(args.pc) if args.pc.strip() else detect_pc_name()

    orch_path = Path(args.orchestrator).expanduser().resolve()
    if not orch_path.exists():
        print(f"ERROR: orchestrator not found: {orch_path}", file=sys.stderr)
        return 2

    # Build orchestrator command
    cmd = [sys.executable, str(orch_path)]
    if not args.once:
        cmd.append("--loop")

    # If user passed args, strip an initial "--" if present (common pattern)
    extra = list(args.orch_args)
    if extra and extra[0] == "--":
        extra = extra[1:]
    cmd.extend(extra)

    # Prepare env with WORKER_PC set
    env = os.environ.copy()
    env["WORKER_PC"] = pc_name
    env.setdefault("PYTHONUNBUFFERED", "1")

    # Print the "equivalent" shell command for clarity
    printable = " ".join(shlex.quote(x) for x in cmd)
    print(f"WORKER_PC={shlex.quote(pc_name)} {printable}")

    if args.print_only:
        return 0

    # Execute
    proc = subprocess.run(cmd, env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
