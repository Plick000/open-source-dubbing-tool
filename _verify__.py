#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ViralVerse Dubbing Automation — Full Verifier (Packages + Apps)
----------
Goal: On a new PC, quickly confirm "nothing is missing" without running the pipeline.

What it checks (dynamic, non-hardcoded):
- Repo structure (find root by markers)
- Python environment (version, venv, pip)
- Python packages (from requirements*.txt pinned == versions + key imports)
- Node environment (node/npm; package.json deps if present)
- Docker environment (docker + compose; only presence/version, not daemon health)
- External binaries commonly used (ffmpeg/ffprobe, gemini, git, convert)
- WSL interoperability (optional): /mnt/c, powershell.exe/cmd.exe presence
- .env keys presence + any configured paths existence (without printing secrets)

Exit code:
- 0: core checks passed (no FAIL)
- 2: failures found
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import importlib.metadata as imd  # py3.8+
except Exception:
    imd = None


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    kind: str = "check"  # check | warn


# -----------------------------
# Helpers
# -----------------------------
def find_root(start: Path) -> Path:
    markers_any = [
        {"requirements.txt", "_pipeline_orchestrator__.py"},
        {"requirements.txt", "docker-compose.yml"},
        {"requirements.txt", "Dockerfile"},
    ]
    cur = start.resolve()
    for _ in range(20):
        for s in markers_any:
            if all((cur / m).exists() for m in s):
                return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise SystemExit("ERROR: Could not locate project root. Run this script inside the repo folder.")


def first_line(cmd: list[str]) -> str | None:
    import subprocess
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        out = (p.stdout or p.stderr or "").strip().splitlines()
        return out[0].strip() if out else "found"
    except Exception:
        return "found"


def bin_version(cmd: str, args: list[str] | None = None) -> str | None:
    if not shutil.which(cmd):
        return None
    if args is None:
        args = ["--version"]
    v = first_line([cmd] + args)
    return v or "found"


def parse_pinned_requirements(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*==\s*([^\s;]+)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if not k:
            continue
        env[k] = v.strip()
    return env


def json_read(path: Path) -> dict:
    try:
        import json as _json
        return _json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


def add(results: list[CheckResult], name: str, ok: bool, detail: str, kind: str = "check") -> None:
    results.append(CheckResult(name=name, ok=ok, detail=detail, kind=kind))


def unique(iterable: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for x in iterable:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Print full JSON report")
    args = ap.parse_args()

    root = find_root(Path(__file__).parent)
    results: list[CheckResult] = []

    # Python / venv
    add(results, "python.version", True, sys.version.split()[0])
    add(results, "python.min_version", sys.version_info >= (3, 10), ">= 3.10 required")
    in_venv = (hasattr(sys, "real_prefix") or (getattr(sys, "base_prefix", sys.prefix) != sys.prefix))
    add(results, "python.venv_active", in_venv, sys.prefix, kind="warn" if not in_venv else "check")
    add(results, "pip.exists", shutil.which("pip") is not None or shutil.which("pip3") is not None,
        shutil.which("pip") or shutil.which("pip3") or "not found")

    # Platform / WSL
    is_wsl = False
    try:
        is_wsl = "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        pass
    add(results, "platform", True, f"{platform.system()} ({'WSL' if is_wsl else 'native'})")

    if is_wsl:
        add(results, "wsl.mnt_c", Path("/mnt/c").exists(), "/mnt/c", kind="warn" if not Path("/mnt/c").exists() else "check")
        add(results, "wsl.powershell.exe", shutil.which("powershell.exe") is not None,
            shutil.which("powershell.exe") or "not found", kind="warn" if shutil.which("powershell.exe") is None else "check")
        add(results, "wsl.cmd.exe", shutil.which("cmd.exe") is not None,
            shutil.which("cmd.exe") or "not found", kind="warn" if shutil.which("cmd.exe") is None else "check")

    # Repo must-haves (minimal, real)
    must = [
        "requirements.txt",
        "_pipeline_orchestrator__.py",
        "__run__.py",
        "Python",
        "templates",
        "Dockerfile",
        "docker-compose.yml",
    ]
    for rel in must:
        p = root / rel
        add(results, f"repo.exists:{rel}", p.exists(), str(p))

    # Templates sanity
    tdir = root / "templates"
    htmls = sorted([p.name for p in tdir.glob("*.html")]) if tdir.exists() else []
    add(results, "templates.html_count", len(htmls) >= 3, f"{len(htmls)} ({', '.join(htmls[:6])})")

    # -------------------------
    # Python packages
    # -------------------------
    req_files = unique(
        [str(root / "requirements.txt")] +
        [str(p) for p in root.glob("requirements*.txt")] +
        [str(p) for p in (root / "requirements").glob("*.txt")] if (root / "requirements").exists() else []
    )
    pinned_total: list[tuple[str, str]] = []
    for rf in req_files:
        p = Path(rf)
        if not p.exists():
            continue
        pins = parse_pinned_requirements(p.read_text(encoding="utf-8", errors="ignore"))
        pinned_total.extend(pins)

    if not pinned_total:
        add(results, "requirements.pinned", False, "No pinned 'pkg==ver' lines found")
    else:
        add(results, "requirements.pinned", True, f"{len(pinned_total)} pinned packages across {len(req_files)} file(s)")

    if imd is None:
        add(results, "python.importlib.metadata", False, "importlib.metadata unavailable")
    else:
        missing = []
        mismatch = []
        for pkg, want in pinned_total:
            try:
                have = imd.version(pkg)
            except Exception:
                missing.append(pkg)
                continue
            if have != want:
                mismatch.append((pkg, want, have))
        add(results, "deps.missing", len(missing) == 0, f"{len(missing)} missing (e.g. {missing[:5]})" if missing else "none")
        if mismatch:
            sample = ", ".join([f"{p} want {w} have {h}" for p, w, h in mismatch[:5]])
            add(results, "deps.version_mismatch", False, f"{len(mismatch)} mismatched; {sample}")
        else:
            add(results, "deps.version_mismatch", True, "none")

    # Import smoke for common modules actually used in pipelines
    # NOTE: If you intentionally don't use one, remove it from this list.
    import_tests = [
        "flask",
        "requests",
        "pydub",
        "av",
        "google.auth",
        "googleapiclient.discovery",
        "dotenv",  # python-dotenv
    ]
    for mod in import_tests:
        try:
            __import__(mod)
            add(results, f"import:{mod}", True, "ok")
        except Exception as e:
            add(results, f"import:{mod}", False, f"{type(e).__name__}: {e}")

    # -------------------------
    # Node packages (if package.json exists)
    # -------------------------
    add(results, "bin:node", bin_version("node", ["-v"]) is not None, bin_version("node", ["-v"]) or "not found")
    add(results, "bin:npm", bin_version("npm", ["-v"]) is not None, bin_version("npm", ["-v"]) or "not found")

    pkg_json = root / "package.json"
    if pkg_json.exists():
        pj = json_read(pkg_json)
        deps = list((pj.get("dependencies") or {}).keys())
        dev_deps = list((pj.get("devDependencies") or {}).keys())
        add(results, "node.package_json", True, f"deps={len(deps)} devDeps={len(dev_deps)}")
        node_modules = root / "node_modules"
        add(results, "node.node_modules_present", node_modules.exists(), str(node_modules), kind="warn" if not node_modules.exists() else "check")
    else:
        add(results, "node.package_json", True, "not used (no package.json)", kind="warn")

    # -------------------------
    # Docker (if used)
    # -------------------------
    docker_needed = (root / "docker-compose.yml").exists() or (root / "Dockerfile").exists()
    if docker_needed:
        dv = bin_version("docker")
        add(results, "bin:docker", dv is not None, dv or "not found")
        # docker compose: either `docker compose` or `docker-compose`
        compose_v = None
        if shutil.which("docker"):
            compose_v = first_line(["docker", "compose", "version"])
        if not compose_v and shutil.which("docker-compose"):
            compose_v = first_line(["docker-compose", "version"])
        add(results, "bin:docker_compose", compose_v is not None, compose_v or "not found", kind="warn" if compose_v is None else "check")

    # -------------------------
    # External apps/binaries
    # -------------------------
    for b, args_ in [
        ("ffmpeg", ["-version"]),
        ("ffprobe", ["-version"]),
        ("git", ["--version"]),
    ]:
        v = bin_version(b, args_)
        add(results, f"bin:{b}", v is not None, v or "not found")

    # Gemini CLI (env GEMINI_BIN or gemini on PATH)
    env_file = root / ".env"
    env_from_file = load_env_file(env_file)
    gemini_bin = os.environ.get("GEMINI_BIN") or env_from_file.get("GEMINI_BIN") or "gemini"
    if gemini_bin and ("/" in gemini_bin or "\\" in gemini_bin):
        ok = Path(gemini_bin).expanduser().exists()
        add(results, "bin:gemini_bin_path", ok, gemini_bin, kind="warn" if not ok else "check")
    else:
        v = bin_version("gemini")
        add(results, "bin:gemini", v is not None, v or "not found", kind="warn" if v is None else "check")

    # ImageMagick optional
    v = bin_version("convert")
    add(results, "bin:convert(ImageMagick)", v is not None, v or "not found", kind="warn" if v is None else "check")

    # -------------------------
    # .env keys (presence only, never print secrets)
    # -------------------------
    add(results, "envfile.exists", env_file.exists(), str(env_file), kind="warn" if not env_file.exists() else "check")
    combined_env = dict(env_from_file)
    for k in ["GEMINI_BIN", "GEMINI_MODEL", "N8N_WEBHOOK_URL", "WORKER_NAME", "WIN_AGENT_TOKEN",
              "WHISPER_DEVICE", "WHISPER_MODEL_DIR", "WHISPER_LOCAL_CACHE",
              "ELEVENLABS_API_KEY", "GEMINI_API_KEY"]:
        if os.environ.get(k):
            combined_env[k] = os.environ.get(k, "")

    required_env = ["GEMINI_MODEL", "N8N_WEBHOOK_URL", "WORKER_NAME", "WHISPER_DEVICE", "WHISPER_MODEL_DIR"]
    optional_env = ["WIN_AGENT_TOKEN", "WHISPER_LOCAL_CACHE", "GEMINI_BIN"]
    secret_env = ["ELEVENLABS_API_KEY", "GEMINI_API_KEY"]

    for k in required_env:
        ok = bool(combined_env.get(k))
        add(results, f"env.required:{k}", ok, "set" if ok else "missing")

    for k in optional_env:
        ok = bool(combined_env.get(k))
        add(results, f"env.optional:{k}", ok, "set" if ok else "missing", kind="warn" if not ok else "check")

    for k in secret_env:
        ok = bool(combined_env.get(k))
        add(results, f"env.secret:{k}", ok, "set" if ok else "missing", kind="warn" if not ok else "check")

    # Any env path keys -> existence check
    for k in ["WHISPER_MODEL_DIR", "WHISPER_LOCAL_CACHE"]:
        v = combined_env.get(k)
        if v:
            exists = Path(v).expanduser().exists()
            add(results, f"path.exists:{k}", exists, v, kind="warn" if not exists else "check")

    # -------------------------
    # Summaries
    # -------------------------
    failures = [r for r in results if (not r.ok and r.kind != "warn")]
    warns = [r for r in results if (not r.ok and r.kind == "warn")]

    if args.json:
        payload = {
            "project_root": str(root),
            "ok": len(failures) == 0,
            "failures": [r.__dict__ for r in failures],
            "warnings": [r.__dict__ for r in warns],
            "all": [r.__dict__ for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        print("=" * 72)
        print("ViralVerse Automation Full Verification Report")
        print(f"Project root: {root}")
        print("=" * 72)
        for r in results:
            tag = "OK " if r.ok else ("WARN" if r.kind == "warn" else "FAIL")
            print(f"[{tag}] {r.name} :: {r.detail}")
        print("-" * 72)
        print(f"Failures: {len(failures)} | Warnings: {len(warns)}")
        print("Exit code:", 2 if failures else 0)

    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
