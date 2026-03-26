#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
__run_jsx__.py

Minimal JSX runner for WSL2 -> Windows Adobe pipeline.

Usage:
  python __run_jsx__.py <absolute_path_to_jsx>

Behavior:
  - Validates JSX exists (WSL path)
  - Converts to Windows path (C:\..., Z:\...)
  - Uses powershell.exe with a timeout to execute a temporary VBS that tells
    Premiere to run the JSX via COM DoScriptFile (when available).
  - Returns exit code 0 on success, non-zero otherwise.

NOTE:
  This is intentionally minimal and blocking-safe (timeouts everywhere).
"""

from __future__ import annotations

import os
import sys
import re
import subprocess
from pathlib import Path

PS_TIMEOUT_SEC = 25

def ps_sq(s: str) -> str:
    return "'" + (s or "").replace("'", "''") + "'"

def run_powershell(ps_cmd: str, timeout: int = PS_TIMEOUT_SEC) -> tuple[int, str]:
    cmd = ["powershell.exe", "-NoProfile", "-Command", ps_cmd]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"PowerShell timeout after {timeout}s"
    except Exception as e:
        return 1, f"PowerShell error: {e!r}"

def wsl_to_win_path(p: str) -> str:
    """
    Convert /mnt/c/... -> C:\...
    Convert /mnt/z/... -> Z:\...
    If already looks like X:\ keep it.
    """
    p = (p or "").strip().strip('"')
    if re.match(r"^[A-Za-z]:[\\/]", p):
        return p.replace("/", "\\")
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace("/", "\\")
        return f"{drive}:\\{rest}"
    return p

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python __run_jsx__.py <path_to_jsx>", file=sys.stderr)
        return 2

    jsx_wsl = sys.argv[1]
    jsx_path = Path(jsx_wsl).expanduser()

    if not jsx_path.is_absolute():
        jsx_path = (Path.cwd() / jsx_path).resolve()

    if not jsx_path.exists():
        print(f"JSX not found: {jsx_path}", file=sys.stderr)
        return 2

    jsx_win = wsl_to_win_path(str(jsx_path))

    # Create a temp VBS on Windows side so cscript can run it reliably
    # We'll write it via PowerShell, then execute it, then delete it.
    # The VBS tries to attach to Premiere and call DoScriptFile().
    vbs_content = r'''
On Error Resume Next
Dim app, jsxPath
jsxPath = WScript.Arguments.Item(0)

Set app = CreateObject("Premiere.Application")
If Err.Number <> 0 Then
  WScript.Echo "ERROR: Could not create Premiere.Application (COM). Err=" & Err.Number
  WScript.Quit 5
End If
Err.Clear

' Run JSX file
app.DoScriptFile jsxPath
If Err.Number <> 0 Then
  WScript.Echo "ERROR: DoScriptFile failed. Err=" & Err.Number
  WScript.Quit 6
End If

WScript.Echo "OK"
WScript.Quit 0
'''.strip()

    # Put VBS in %TEMP%
    ps = f"""
$ErrorActionPreference = 'Stop'
$vbspath = Join-Path $env:TEMP ('vv_run_jsx_' + [Guid]::NewGuid().ToString('N') + '.vbs')
@"
{vbs_content}
"@ | Set-Content -Path $vbspath -Encoding ASCII
try {{
  $out = & cscript.exe //nologo $vbspath {ps_sq(jsx_win)} 2>&1
  $ec = $LASTEXITCODE
  Write-Output $out
  exit $ec
}} finally {{
  Remove-Item -Force $vbspath -ErrorAction SilentlyContinue
}}
""".strip()

    code, out = run_powershell(ps, timeout=PS_TIMEOUT_SEC)
    out = (out or "").strip()

    if out:
        print(out)

    return code

if __name__ == "__main__":
    raise SystemExit(main())