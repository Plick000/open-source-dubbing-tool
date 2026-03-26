# dubbing_tool/__version__.py
"""
Currently version of the

ViralVerse Dubbing Automation
======

(Experimental) v1.0.0
---------
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os

# Single source of truth (SemVer): MAJOR.MINOR.PATCH
__version__ = "(Experimental) v1.0.0"

@dataclass(frozen=True)
class VersionInfo:
    version: str
    build_date_utc: str | None
    git_commit: str | None

def get_version_info() -> VersionInfo:
    """
    Optional build metadata (safe defaults).
    You can inject these as environment variables in your runner / n8n / docker.
    """
    build_date = os.getenv("DUBBING_BUILD_DATE_UTC")  # e.g. "2026-01-07T12:30:00Z"
    git_commit = os.getenv("DUBBING_GIT_COMMIT")      # e.g. "a1b2c3d"

    # If build_date not provided, you can leave it None OR generate it at runtime.
    # Runtime generation is okay for logs, but not a true "build" timestamp.
    if not build_date:
        # Comment this out if you prefer None always.
        build_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return VersionInfo(
        version=__version__,
        build_date_utc=build_date,
        git_commit=git_commit
    )


print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] || ViralVerse Dubbing Tool - {__version__}")