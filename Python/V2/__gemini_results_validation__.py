#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import time
import subprocess
from pathlib import Path
from typing import Any, List, Tuple

MAX_VALIDATION_TRIES = 3
MAX_TRANSLATION_TRIES = 3
SLEEP_SECONDS = 1.0

BAD_LITERALS = {"", "null", "none", "nan", "undefined"}


def _is_nonempty_text(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    if s.lower() in BAD_LITERALS:
        return False
    return True


def _validate_json(json_path: Path) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    if not json_path.exists():
        return False, [f"JSON not found: {json_path}"]

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"JSON parse failed: {e!r}"]

    if not isinstance(data, list) or not data:
        return False, ["JSON root must be a non-empty list (array)."]

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"[index {i}] item is not an object/dict")
            continue

        if "exact_script_text" not in item:
            errors.append(f"[index {i} id={item.get('id')}] missing exact_script_text")
            continue

        if not _is_nonempty_text(item.get("exact_script_text")):
            errors.append(f"[index {i} id={item.get('id')}] exact_script_text missing/empty/invalid")

    return (len(errors) == 0), errors


def _pick_existing_json_path(root: Path) -> Path:
    candidates = [
        root / "output" / "JSON" / "A12__final_segments__.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _pick_segments_validator_script(here: Path) -> Path:
    candidates = [
        here / "__gemini_segments_validations__.py",
        here / "__gemini_segments_validation__.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _pick_translation_cli_script(here: Path, root: Path) -> Path:
    """Best-effort locator for __gemini_translation_cli__.py without breaking existing layout."""
    candidates = [
        here / "__gemini_translation_cli__.py",
        root / "__gemini_translation_cli__.py",
        root / "scripts" / "__gemini_translation_cli__.py",
        root / "tools" / "__gemini_translation_cli__.py",
        root / "pipeline" / "__gemini_translation_cli__.py",
    ]
    for p in candidates:
        if p.exists():
            return p

    # Last-resort: shallow-ish scan (kept conservative to avoid slow full-repo walk)
    try:
        for p in root.rglob("__gemini_translation_cli__.py"):
            if p.is_file():
                return p
    except Exception:
        pass

    return candidates[0]


def _run_segments_validator(script_path: Path, cwd: Path) -> int:
    if not script_path.exists():
        print(f"[ERROR] Segments validator not found: {script_path}\n")
        return 127

    cmd = [sys.executable, str(script_path)]
    print(f"[RUN] {' '.join(cmd)}")
    print("\n")
    p = subprocess.run(cmd, cwd=str(cwd), text=True)
    return p.returncode


def _run_translation_cli(script_path: Path, cwd: Path) -> int:
    if not script_path.exists():
        print(f"[ERROR] Translation CLI not found: {script_path}\n")
        return 127

    cmd = [sys.executable, str(script_path)]
    print(f"[RUN] {' '.join(cmd)}")
    print("\n")
    p = subprocess.run(cmd, cwd=str(cwd), text=True)
    return p.returncode


def _run_validation_cycle(json_path: Path, segments_script: Path, root: Path, label: str) -> int:
    """Return 0 if OK, 1 if validation exhausted, 2 if validator script errored."""
    for attempt in range(1, MAX_VALIDATION_TRIES + 1):
        ok, errs = _validate_json(json_path)

        if ok:
            print(f"[OK] {label}: Validation passed: {json_path}")
            return 0

        print(f"[FAIL] {label}: Validation failed (attempt {attempt}/{MAX_VALIDATION_TRIES}).")
        for e in errs[:50]:
            print(" - " + e)

        if attempt >= MAX_VALIDATION_TRIES:
            return 1  # exhausted validation attempts

        # Try to fix by running segments validator
        rc = _run_segments_validator(segments_script, cwd=root)
        if rc != 0:
            print(f"[ERROR] Segments validator returned non-zero exit code: {rc}")
            return 2

        # Re-detect JSON location (in case it was created in output/JSON)
        json_path = _pick_existing_json_path(root)
        time.sleep(SLEEP_SECONDS)

    return 1


def main() -> int:
    here = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parents[2]

    json_path = _pick_existing_json_path(root)
    segments_script = _pick_segments_validator_script(here)

    # -------------------------
    # Cycle 1: validate + segments-validator self-heal (up to MAX_VALIDATION_TRIES)
    # -------------------------
    cycle1 = _run_validation_cycle(
        json_path=json_path,
        segments_script=segments_script,
        root=root,
        label="SEGMENTS VALIDATION",
    )
    if cycle1 == 0:
        return 0
    if cycle1 == 2:
        return 1  # keep existing behavior: stop if validator script errors

    # -------------------------
    # If still failing after 3 tries:
    # Run __gemini_translation_cli__.py (up to MAX_TRANSLATION_TRIES),
    # then re-run the validation cycle again (up to MAX_VALIDATION_TRIES).
    # -------------------------
    translation_script = _pick_translation_cli_script(here, root)
    print(f"[WARN] Validation failed after {MAX_VALIDATION_TRIES} attempts.")
    print("[WARN] Running translation regeneration short-cycle: __gemini_translation_cli__.py")

    last_rc = 1
    for t in range(1, MAX_TRANSLATION_TRIES + 1):
        rc = _run_translation_cli(translation_script, cwd=root)
        last_rc = rc
        if rc == 0:
            print(f"[OK] Translation CLI succeeded (attempt {t}/{MAX_TRANSLATION_TRIES}).")
            break
        print(f"[FAIL] Translation CLI failed (attempt {t}/{MAX_TRANSLATION_TRIES}) with exit code: {rc}")
        if t < MAX_TRANSLATION_TRIES:
            time.sleep(SLEEP_SECONDS)

    if last_rc != 0:
        print(f"[ERROR] Translation CLI failed after {MAX_TRANSLATION_TRIES} attempts. Stopping.")
        return 1

    # Cycle 2: validate again (same self-heal behavior)
    json_path = _pick_existing_json_path(root)
    cycle2 = _run_validation_cycle(
        json_path=json_path,
        segments_script=segments_script,
        root=root,
        label="SEGMENTS VALIDATION (POST-TRANSLATION)",
    )
    if cycle2 == 0:
        return 0
    if cycle2 == 2:
        return 1

    print("[ERROR] Validation failed after translation regeneration + 3 more attempts. Stopping.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
