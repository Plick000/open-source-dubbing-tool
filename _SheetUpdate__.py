#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations


import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# ----------------------------
# Helpers
# ----------------------------


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        die(f"json not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"failed to read json: {path} :: {e}")
    return {}


def find_project_root(start: Path) -> Path:
    """
    Find the project root by walking up until we see `admin/` and `Python/` or `inputs/`.
    Falls back to the script directory if not found.
    """
    cur = start.resolve()
    for p in [cur] + list(cur.parents):
        if (p / "admin").is_dir() and ((p / "Python").is_dir() or (p / "inputs").is_dir()):
            return p
    return start.resolve()


def resolve_path(project_root: Path, maybe_path: str) -> Path:
    s = str(maybe_path or "").strip()
    if not s:
        return Path("")
    p = Path(s)
    if p.is_absolute():
        return p
    return (project_root / p).resolve()


def read_done_job(done_json_path: Path) -> Dict[str, Any]:
    raw = load_json(done_json_path)
    if isinstance(raw, list):
        if not raw or not isinstance(raw[0], dict):
            die("done json is list but not list-of-objects")
        return raw[0]
    if not isinstance(raw, dict):
        die("done json is not an object")
    return raw


def extract_row_number(job: Dict[str, Any]) -> Optional[int]:
    # common places you used earlier
    candidates = [
        job.get("row_number"),
        (job.get("config") or {}).get("row_number"),
        (job.get("job") or {}).get("row_number"),
    ]
    for v in candidates:
        if v is None:
            continue
        try:
            n = int(v)
            if n > 0:
                return n
        except Exception:
            pass
    return None


def col_to_index(col_letter: str) -> int:
    """A -> 1, B -> 2, ..."""
    col_letter = col_letter.strip().upper()
    if not col_letter or not col_letter.isalpha():
        die(f"invalid column letter: {col_letter}")
    n = 0
    for ch in col_letter:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def index_to_col(idx: int) -> str:
    """1 -> A, 2 -> B, ..."""
    if idx <= 0:
        die(f"invalid column index: {idx}")
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(r + ord("A")) + s
    return s


def get_sheet_id_by_name(service, spreadsheet_id: str, sheet_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props.get("sheetId"))
    die(f"sheet not found by name: {sheet_name}")
    return -1


def find_column_by_header(service, spreadsheet_id: str, sheet_name: str, header: str) -> Tuple[str, int]:
    """
    Reads the first row and finds the column index where cell == header (case-sensitive by default).
    Returns (col_letter, col_index_1based).
    """
    rng = f"{sheet_name}!1:1"
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=rng,
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    values = (resp.get("values") or [[]])[0]
    for i, v in enumerate(values, start=1):
        if str(v).strip() == str(header).strip():
            return index_to_col(i), i
    die(f"header not found in first row: '{header}'")
    return ("", 0)


def build_sheets_service_from_config(project_root: Path, sheets_cfg: Dict[str, Any]):
    """
    OPTION C: Prefer credentials.json_path from config.json (relative to project root).
    Only fallback to env var if config has no json_path.
    """
    creds_cfg = (sheets_cfg.get("credentials") or {})
    json_path_cfg = str(creds_cfg.get("json_path") or "").strip()

    cred_path = resolve_path(project_root, json_path_cfg) if json_path_cfg else Path("")

    if cred_path and cred_path.exists():
        creds = Credentials.from_service_account_file(
            str(cred_path),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False), cred_path

    # Fallback (optional)
    envp = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if envp:
        env_path = Path(envp).expanduser().resolve()
        if not env_path.exists():
            die(f"GOOGLE_APPLICATION_CREDENTIALS points to missing file: {env_path}")
        creds = Credentials.from_service_account_file(
            str(env_path),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False), env_path

    die("No credentials provided. Add credentials.json_path in sheets config.json (recommended).")
    return None, Path("")


# ----------------------------
# Main
# ----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--done-json", required=True, help="Path to done job json (in JOBS/done)")
    ap.add_argument(
        "--sheets-config",
        default="admin/configs/sheets/config.json",
        help="Path to sheets config json (relative to project root by default)",
    )
    ap.add_argument("--header", default="", help="Column header to update (overrides config status_header)")
    ap.add_argument("--value", default="", help="Value to write (overrides config done_value)")

    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    project_root = find_project_root(script_dir)

    done_json_path = Path(args.done_json).expanduser()
    if not done_json_path.is_absolute():
        done_json_path = (Path.cwd() / done_json_path).resolve()
    if not done_json_path.exists():
        die(f"done json not found: {done_json_path}")

    sheets_config_path = resolve_path(project_root, args.sheets_config)
    sheets_cfg = load_json(sheets_config_path)

    spreadsheet_id = str(sheets_cfg.get("spreadsheet_id") or "").strip()
    sheet_name = str(sheets_cfg.get("sheet_name") or "").strip()
    if not spreadsheet_id:
        die("missing spreadsheet_id in sheets config.json")
    if not sheet_name:
        die("missing sheet_name in sheets config.json")

    update_mode = str(sheets_cfg.get("update_mode") or "header").strip().lower()
    cfg_header = str(sheets_cfg.get("status_header") or "").strip()
    cfg_col_letter = str(sheets_cfg.get("status_column_letter") or "").strip()
    cfg_done_value = str(sheets_cfg.get("done_value") or "Done").strip()

    header = (args.header or cfg_header).strip()
    value = (args.value or cfg_done_value).strip()

    job = read_done_job(done_json_path)
    row_number = extract_row_number(job)
    if not row_number:
        die("row_number not found in done json (expected at top-level row_number or config.row_number/job.row_number)")

    service, used_cred_path = build_sheets_service_from_config(project_root, sheets_cfg)
    sheets = service.spreadsheets()

    # choose column
    if update_mode == "header":
        if not header:
            die("update_mode=header but no --header provided and no status_header in config.json")
        col_letter, col_idx = find_column_by_header(service, spreadsheet_id, sheet_name, header)
    elif update_mode == "column_letter":
        if not cfg_col_letter:
            die("update_mode=column_letter but status_column_letter missing in config.json")
        col_letter = cfg_col_letter.strip().upper()
        col_idx = col_to_index(col_letter)
    else:
        die(f"invalid update_mode in config.json: {update_mode} (use 'header' or 'column_letter')")

    # update cell
    rng = f"{sheet_name}!{col_letter}{row_number}"
    sheets.values().update(
        spreadsheetId=spreadsheet_id,
        range=rng,
        valueInputOption="USER_ENTERED",
        body={"values": [[value]]},
    ).execute()

    print("UPDATED GOOGLE SHEET")
    print(f"  Config: {sheets_config_path}")
    print(f"  Credentials: {used_cred_path}")
    print(f"  Spreadsheet: {spreadsheet_id}")
    print(f"  Sheet: {sheet_name}")
    print(f"  Row: {row_number}")
    print(f"  Column: {header if update_mode=='header' else col_letter} ({col_letter})")
    print(f"  Value: {value}")
    print(f"  Done JSON: {done_json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
