#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
_report_generator__.py
Reads a JOBS.zip, extracts it, and generates a clean Markdown report:

  _Report__YYYYMMDDHH__.md   (UTC hour in filename)

Includes:
- Job-level table (one row per job json)
- Language-level table (one row per language)
- Summary by language
- Status + Reason (failure reason best-effort)

Safe: DOES NOT modify ZIP or job files. Only reads + writes the report.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# -----------------------
# Regex / parsing
# -----------------------

ERROR_PAT = re.compile(r"(Traceback|ERROR|Exception|❌|FAIL|FATAL|Invalid data)", re.IGNORECASE)
FAIL_WORD_PAT = re.compile(r"(fail|failed|error|exception|fatal|quota|corrupt|invalid)", re.IGNORECASE)

# Common JSON keys where a "reason" might exist
REASON_KEYS = [
    "reason", "Reason",
    "error", "Error",
    "err", "Err",
    "message", "Message",
    "failure_reason", "FailureReason", "fail_reason", "FailReason",
    "last_error", "LastError",
    "exception", "Exception",
    "details", "Details",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def fmt_report_stamp(now_utc: dt.datetime) -> str:
    # Required naming: _Report__YYYYMMDDHH__.md
    return now_utc.strftime("%Y%m%d%H")


def parse_iso_to_utc(iso: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None


def parse_claimed_worker(path_str: str) -> Optional[str]:
    # Example: ...__CLAIMED__Room-2-Sufiyan__20260130_113811__...
    m = re.search(r"__CLAIMED__([^_]+(?:-[^_]+)*)__\d{8}_\d{6}__", path_str)
    return m.group(1) if m else None


def parse_log_ts_from_name(name: str) -> Optional[dt.datetime]:
    # Example: Video__20260130_113812.log
    m = re.search(r"__(\d{8})_(\d{6})\.log$", name)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)


def escape_pipes(s: str) -> str:
    return str(s).replace("|", "\\|")


def fmt_dt(dt_utc: Optional[dt.datetime], tz: str) -> str:
    if not dt_utc:
        return ""
    tz = tz.upper().strip()
    if tz == "PKT":
        pkt = dt_utc.astimezone(dt.timezone(dt.timedelta(hours=5)))
        return pkt.strftime("%Y-%m-%d %H:%M:%S PKT")
    return dt_utc.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _clean_reason(s: str, max_len: int = 160) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def _get_first_reason_from_obj(obj: Any) -> str:
    """
    Try to extract a reason string from a dict-like object.
    """
    if not isinstance(obj, dict):
        return ""

    # direct keys
    for k in REASON_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return _clean_reason(v)
        if isinstance(v, dict):
            # sometimes nested dict: {"error": {"message": "..."}}
            for kk in REASON_KEYS:
                vv = v.get(kk)
                if isinstance(vv, str) and vv.strip():
                    return _clean_reason(vv)

    # common nested patterns
    for nested_key in ["result", "status", "meta", "debug", "logs"]:
        v = obj.get(nested_key)
        if isinstance(v, dict):
            r = _get_first_reason_from_obj(v)
            if r:
                return r

    return ""


def is_failed(state: str, status: str, reason: str) -> bool:
    """
    Failed includes:
    - skipped (your requirement)
    - unknown (usually broken)
    - status contains fail/error/exception
    - reason looks error-ish
    """
    state_l = (state or "").lower()
    status_l = (status or "").lower()
    reason_l = (reason or "").lower()

    # ✅ treat skipped as failed
    if state_l == "skipped":
        return True

    # existing logic
    if FAIL_WORD_PAT.search(status_l):
        return True
    if reason and FAIL_WORD_PAT.search(reason_l):
        return True
    if state_l == "unknown":
        return True
    return False


def infer_reason_from_text(text: str) -> str:
    """
    Extract a best-effort reason from a log/status text:
    - Prefer the LAST matching error-like line in the last N lines.
    """
    if not text:
        return ""

    lines = text.splitlines()
    tail = lines[-250:]  # inspect last chunk
    candidates = [ln.strip() for ln in tail if ERROR_PAT.search(ln)]
    if candidates:
        return _clean_reason(candidates[-1])

    # fallback: look for generic fail words in last lines
    fallback = [ln.strip() for ln in tail if FAIL_WORD_PAT.search(ln)]
    if fallback:
        return _clean_reason(fallback[-1])

    return ""


# -----------------------
# Data model
# -----------------------

@dataclass
class JobRow:
    path: str
    state: str
    video: str
    brand: str
    job_id: str
    worker: str
    pc: str
    languages: List[str]
    created_at_utc: Optional[dt.datetime]
    status: str
    process: str
    reason: str  # <--- NEW


@dataclass
class LanguageRow:
    state: str
    brand: str
    video: str
    job_id: str
    worker: str
    pc: str
    language: str
    created_at_utc: Optional[dt.datetime]
    status: str
    reason: str  # <--- NEW
    json_path: str


# -----------------------
# ZIP handling
# -----------------------

def extract_zip(zip_path: Path, extract_dir: Path) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)


def detect_state_from_relpath(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    top = p.split("/", 1)[0].lower()
    if top == "pending":
        return "pending"
    if top == "processing":
        if "/skipped/" in p.lower():
            return "skipped"
        return "processing"
    if top == "done":
        return "done"
    return "unknown"


def load_job_json(json_path: Path) -> Optional[Dict[str, Any]]:
    """
    Your job JSONs often look like: [ { ... } ]
    We'll read the first dict (or dict directly).
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def collect_jobs(extract_dir: Path) -> List[JobRow]:
    jobs: List[JobRow] = []

    for json_path in extract_dir.rglob("*.json"):
        rel = str(json_path.relative_to(extract_dir)).replace("\\", "/")
        state = detect_state_from_relpath(rel)

        entry = load_job_json(json_path)
        if not entry:
            continue

        job_obj = entry.get("job") if isinstance(entry.get("job"), dict) else {}

        video = str(entry.get("video_name") or job_obj.get("video_name") or json_path.stem)
        brand = str(entry.get("Brand") or job_obj.get("brand") or "")
        job_id = str(entry.get("job_id") or job_obj.get("job_id") or "")
        pc = str(entry.get("PC") or job_obj.get("pc") or "")

        languages = job_obj.get("languages") if isinstance(job_obj.get("languages"), list) else []
        languages = [str(x) for x in languages if str(x).strip()]

        created_at_utc = None
        if isinstance(job_obj.get("created_at"), str):
            created_at_utc = parse_iso_to_utc(job_obj["created_at"])

        # Worker inference
        worker = parse_claimed_worker(rel) or ""
        parts = rel.split("/")
        # Processing/0workers/<WorkerName>/...
        if len(parts) >= 3 and parts[0].lower() == "processing" and parts[1].lower() == "0workers":
            worker = worker or parts[2]

        process = str(entry.get("Process") or "")
        status = str(entry.get("Status") or process or "")

        # Reason best-effort (JSON only here; log/status enrichment happens later)
        reason = _get_first_reason_from_obj(entry) or _get_first_reason_from_obj(job_obj)

        jobs.append(
            JobRow(
                path=rel,
                state=state,
                video=video,
                brand=brand,
                job_id=job_id,
                worker=worker,
                pc=pc,
                languages=languages,
                created_at_utc=created_at_utc,
                status=status,
                process=process,
                reason=reason,
            )
        )

    return jobs


def collect_latest_logs(extract_dir: Path) -> Dict[str, Path]:
    """
    Best-effort mapping: video -> latest log file, based on timestamp in name:
      <video>__YYYYMMDD_HHMMSS.log
    """
    by_video: Dict[str, List[tuple[Optional[dt.datetime], Path]]] = defaultdict(list)

    for log_path in extract_dir.rglob("*.log"):
        base = log_path.name
        video = base.rsplit("__", 1)[0]
        ts = parse_log_ts_from_name(base)
        by_video[video].append((ts, log_path))

    latest: Dict[str, Path] = {}
    for video, lst in by_video.items():
        lst_sorted = sorted(lst, key=lambda x: (x[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc)))
        latest[video] = lst_sorted[-1][1]
    return latest


def collect_status_files(extract_dir: Path) -> Dict[str, Path]:
    """
    workers/<worker>/status/<video>.txt
    """
    out: Dict[str, Path] = {}
    for st_path in extract_dir.rglob("*.txt"):
        p = str(st_path).replace("\\", "/").lower()
        if "/status/" not in p:
            continue
        video = st_path.stem
        out[video] = st_path
    return out


def enrich_job_reasons_from_logs_and_status(
    jobs: List[JobRow],
    extract_root: Path,
    latest_logs: Dict[str, Path],
    status_files: Dict[str, Path],
) -> None:
    """
    For jobs that look failed but have no reason, try to pull a reason from:
    1) status_files[video]
    2) latest_logs[video]
    """
    # Pre-read status reasons per video
    status_reason_by_video: Dict[str, str] = {}
    for video, p in status_files.items():
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            r = infer_reason_from_text(txt)
            if r:
                status_reason_by_video[video] = r
        except Exception:
            pass

    # Pre-read log reasons per video
    log_reason_by_video: Dict[str, str] = {}
    for video, p in latest_logs.items():
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            r = infer_reason_from_text(txt)
            if r:
                log_reason_by_video[video] = r
        except Exception:
            pass

    # Enrich
    for i, j in enumerate(jobs):
        failed = is_failed(j.state, j.status, j.reason)
        if not failed:
            continue
        if j.reason:
            continue

        r = status_reason_by_video.get(j.video) or log_reason_by_video.get(j.video) or ""
        if r:
            jobs[i].reason = r


# -----------------------
# Language flattening
# -----------------------

def flatten_jobs_to_languages(jobs: List[JobRow]) -> List[LanguageRow]:
    out: List[LanguageRow] = []
    for j in jobs:
        langs = j.languages if j.languages else ["(no_language_listed)"]
        for lang in langs:
            out.append(
                LanguageRow(
                    state=j.state,
                    brand=j.brand,
                    video=j.video,
                    job_id=j.job_id,
                    worker=j.worker,
                    pc=j.pc,
                    language=str(lang),
                    created_at_utc=j.created_at_utc,
                    status=j.status,
                    reason=j.reason,
                    json_path=j.path,
                )
            )
    return out


# -----------------------
# Markdown rendering
# -----------------------

def render_markdown_report(
    zip_path: Path,
    extract_root: Path,
    jobs: List[JobRow],
    latest_logs: Dict[str, Path],
    status_files: Dict[str, Path],
    tz: str,
    now_utc: dt.datetime,
) -> str:
    counts = Counter(j.state for j in jobs)
    total = len(jobs)

    state_order = {"processing": 0, "pending": 1, "skipped": 2, "done": 3, "unknown": 4}

    jobs_sorted = sorted(
        jobs,
        key=lambda j: (state_order.get(j.state, 9), j.brand or "", j.video or "", j.worker or ""),
    )

    lang_rows = flatten_jobs_to_languages(jobs)

    # Summary by language
    lang_state_counts: Dict[str, Counter] = defaultdict(Counter)
    lang_failed_counts: Dict[str, int] = defaultdict(int)

    for r in lang_rows:
        lang_state_counts[r.language][r.state] += 1
        if is_failed(r.state, r.status, r.reason):
            lang_failed_counts[r.language] += 1

    lang_totals = {lang: sum(c.values()) for lang, c in lang_state_counts.items()}
    langs_sorted = sorted(lang_totals.keys(), key=lambda k: (-lang_totals[k], k.lower()))

    lines: List[str] = []
    lines.append("# Jobs Report")
    lines.append("")
    lines.append(f"- **Generated:** {fmt_dt(now_utc, tz)}")
    lines.append(f"- **Source ZIP:** `{zip_path.name}`")
    lines.append(f"- **Total job JSON files:** **{total}**")
    lines.append("")

    # ------------------ Summary (job states) ------------------
    lines.append("## Summary")
    lines.append("")
    lines.append("| State | Count |")
    lines.append("|---|---:|")
    for st in ["pending", "processing", "skipped", "done", "unknown"]:
        if counts.get(st, 0):
            lines.append(f"| {st} | {counts[st]} |")
    lines.append("")

    # ------------------ Summary by language ------------------
    lines.append("## Summary by language")
    lines.append("")
    lines.append("| Language | Total | Pending | Processing | Skipped | Done | Unknown | Failed* |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for lang in langs_sorted:
        c = lang_state_counts[lang]
        lines.append(
            f"| {escape_pipes(lang)} | {lang_totals[lang]} | "
            f"{c.get('pending',0)} | {c.get('processing',0)} | {c.get('skipped',0)} | {c.get('done',0)} | {c.get('unknown',0)} | "
            f"{lang_failed_counts.get(lang,0)} |"
        )
    lines.append("")
    lines.append("*Failed = status/reason/log indicates failure (best-effort heuristic).")
    lines.append("")

    # ------------------ Jobs table (job-level) ------------------
    lines.append("## Jobs (one row per job JSON)")
    lines.append("")
    lines.append("| State | Brand | Video | Job ID | Worker | PC | Langs | Created | Status | Reason | JSON |")
    lines.append("|---|---|---|---|---|---|---:|---|---|---|---|")
    for j in jobs_sorted:
        reason = ""
        if is_failed(j.state, j.status, j.reason):
            reason = j.reason or ("Skipped by system" if (j.state or "").lower() == "skipped" else "")        
        lines.append(
            "| {state} | {brand} | {video} | {job_id} | {worker} | {pc} | {langs} | {created} | {status} | {reason} | {jsonp} |".format(
                state=escape_pipes(j.state),
                brand=escape_pipes(j.brand),
                video=escape_pipes(j.video),
                job_id=escape_pipes(j.job_id),
                worker=escape_pipes(j.worker),
                pc=escape_pipes(j.pc),
                langs=len(j.languages),
                created=escape_pipes(fmt_dt(j.created_at_utc, tz)),
                status=escape_pipes(j.status),
                reason=escape_pipes(reason),
                jsonp=f"`{escape_pipes(j.path)}`",
            )
        )
    lines.append("")

    # ------------------ Languages table (language-level) ------------------
    lines.append("## Languages (one row per language)")
    lines.append("")
    lines.append("| State | Brand | Video | Language | Job ID | Worker | PC | Created | Status | Reason | JSON |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")

    lang_rows_sorted = sorted(
        lang_rows,
        key=lambda r: (state_order.get(r.state, 9), r.brand or "", r.video or "", r.language.lower()),
    )

    for r in lang_rows_sorted:
        reason = ""
        if is_failed(r.state, r.status, r.reason):
            reason = r.reason or ("Skipped by system" if (r.state or "").lower() == "skipped" else "")
        lines.append(
            "| {state} | {brand} | {video} | {language} | {job_id} | {worker} | {pc} | {created} | {status} | {reason} | {jsonp} |".format(
                state=escape_pipes(r.state),
                brand=escape_pipes(r.brand),
                video=escape_pipes(r.video),
                language=escape_pipes(r.language),
                job_id=escape_pipes(r.job_id),
                worker=escape_pipes(r.worker),
                pc=escape_pipes(r.pc),
                created=escape_pipes(fmt_dt(r.created_at_utc, tz)),
                status=escape_pipes(r.status),
                reason=escape_pipes(reason),
                jsonp=f"`{escape_pipes(r.json_path)}`",
            )
        )

    lines.append("")

    # ------------------ Per-video details ------------------
    lines.append("## Per-video details")
    lines.append("")
    videos = sorted({j.video for j in jobs if j.video})

    for v in videos:
        v_jobs = [j for j in jobs if j.video == v]
        st_counts = Counter(j.state for j in v_jobs)
        summary_bits = ", ".join(
            f"{k}:{st_counts[k]}" for k in sorted(st_counts.keys(), key=lambda x: state_order.get(x, 9))
        )

        # best reason for video if any job failed
        v_reasons = [j.reason for j in v_jobs if is_failed(j.state, j.status, j.reason) and j.reason]
        v_reason = _clean_reason(v_reasons[0]) if v_reasons else ""

        lines.append("<details>")
        lines.append(f"<summary><strong>{escape_pipes(v)}</strong> — {summary_bits}</summary>")
        lines.append("")

        if v_reason:
            lines.append(f"- **Failure reason (best):** `{escape_pipes(v_reason)}`")

        for j in v_jobs:
            lang_list = ", ".join(j.languages) if j.languages else "(no_language_listed)"
            failed = is_failed(j.state, j.status, j.reason)
            reason = f" | **Reason:** {escape_pipes(j.reason)}" if (failed and j.reason) else ""
            lines.append(
                f"- **State:** {j.state} | **Brand:** {escape_pipes(j.brand)} | **Worker:** {escape_pipes(j.worker)} | **Languages:** {escape_pipes(lang_list)} | **Status:** {escape_pipes(j.status)}{reason}"
            )
            if j.job_id:
                lines.append(f"  - Job ID: `{escape_pipes(j.job_id)}`")
            lines.append(f"  - JSON: `{escape_pipes(j.path)}`")

        # Latest log tail + error hits
        log_path = latest_logs.get(v)
        if log_path and log_path.exists():
            content = log_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(content.splitlines()[-30:])
            err_hits = [ln.strip() for ln in content.splitlines() if ERROR_PAT.search(ln)]

            rel_log = str(log_path.relative_to(extract_root)).replace("\\", "/")
            lines.append("")
            lines.append(f"**Latest log:** `{escape_pipes(rel_log)}`")

            if err_hits:
                lines.append("")
                lines.append("**Possible errors (first 10 matches):**")
                for ln in err_hits[:10]:
                    lines.append(f"- `{escape_pipes(_clean_reason(ln, 220))}`")

            lines.append("")
            lines.append("**Log tail (last 30 lines):**")
            lines.append("")
            lines.append("```text")
            lines.append(tail)
            lines.append("```")

        # Status file
        st_file = status_files.get(v)
        if st_file and st_file.exists():
            rel_st = str(st_file.relative_to(extract_root)).replace("\\", "/")
            st_txt = st_file.read_text(encoding="utf-8", errors="replace").strip()

            lines.append("")
            lines.append(f"**Worker status file:** `{escape_pipes(rel_st)}`")
            lines.append("")
            lines.append("```text")
            lines.append(st_txt)
            lines.append("```")

        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


# -----------------------
# Public API (what you import)
# -----------------------

def generate_report(
    zip_path: str | Path,
    out_dir: str | Path = "reports",
    tz: str = "UTC",
    keep_extract: bool = False,
) -> Path:
    """
    Generate a Markdown report from a JOBS.zip.

    Args:
      zip_path: path to JOBS.zip
      out_dir: folder to write report into
      tz: "UTC" or "PKT" (only affects displayed timestamps, not filename)
      keep_extract: if True, also copy extracted ZIP content into out_dir/_extracted__YYYYMMDDHH

    Returns:
      Path to the generated markdown report file.
    """
    tz = tz.upper().strip()
    if tz not in ("UTC", "PKT"):
        raise ValueError("tz must be 'UTC' or 'PKT'")

    zip_path = Path(zip_path).expanduser().resolve()
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP not found: {zip_path}")

    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    now_utc = utc_now()
    stamp = fmt_report_stamp(now_utc)
    report_name = f"_Report__{stamp}__.md"
    out_path = out_dir / report_name

    tmp_dir = Path(tempfile.mkdtemp(prefix="jobs_zip_extract_"))
    try:
        extract_zip(zip_path, tmp_dir)

        jobs = collect_jobs(tmp_dir)
        latest_logs = collect_latest_logs(tmp_dir)
        status_files = collect_status_files(tmp_dir)

        # NEW: enrich missing failure reasons from status/logs
        enrich_job_reasons_from_logs_and_status(jobs, tmp_dir, latest_logs, status_files)

        md = render_markdown_report(
            zip_path=zip_path,
            extract_root=tmp_dir,
            jobs=jobs,
            latest_logs=latest_logs,
            status_files=status_files,
            tz=tz,
            now_utc=now_utc,
        )
        out_path.write_text(md, encoding="utf-8")

        if keep_extract:
            kept_dir = out_dir / f"_extracted__{stamp}"
            if kept_dir.exists():
                shutil.rmtree(kept_dir, ignore_errors=True)
            shutil.copytree(tmp_dir, kept_dir, dirs_exist_ok=True)

        return out_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)