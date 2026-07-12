"""Persistent diagnostics and privacy-safe support bundles.

The desktop build has no console, so silent background failures are otherwise
impossible to investigate.  Logging is automatic, bounded, and local.  Support
bundles intentionally exclude journals, commander names, auth secrets and the
market databases.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sqlite3
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import marketdb

LOG_DIR = marketdb.DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "frameshift.log"
BUNDLE_DIR = marketdb.DATA_DIR / "diagnostics"
_configured = False
_lock = threading.Lock()
_bundle_lock = threading.Lock()


def configure(level: int = logging.INFO) -> Path:
    """Configure root logging once and return the persistent log path."""
    global _configured
    with _lock:
        if _configured:
            return LOG_PATH
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=2 * 1024 * 1024,
            backupCount=4,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)sZ %(levelname)s %(name)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(handler)
        _configured = True
        logging.getLogger(__name__).info(
            "Frameshift diagnostics started version=%s python=%s platform=%s frozen=%s",
            _version(), platform.python_version(), platform.platform(), bool(getattr(sys, "frozen", False)),
        )
        return LOG_PATH


def _version() -> str:
    try:
        from ._version import VERSION
        return VERSION
    except Exception:
        return "unknown"


def log_exception(logger: logging.Logger, context: str, exc: BaseException) -> None:
    """Log a caught background exception without leaking arbitrary payloads."""
    logger.warning("%s failed: %s: %s", context, type(exc).__name__, str(exc)[:300], exc_info=True)


def health_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": _version(),
        "time": int(time.time()),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "data_dir_writable": os.access(marketdb.DATA_DIR, os.W_OK),
        "log_path": str(LOG_PATH),
    }
    try:
        conn = marketdb.connect()
        try:
            result["market_database"] = marketdb.status(conn)
            result["sqlite_integrity"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        result["market_database_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    try:
        from .eddn import LISTENER
        result["eddn"] = LISTENER.stats()
    except Exception as exc:
        result["eddn_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    try:
        from .extensions import EXTENSIONS
        result["extensions"] = EXTENSIONS.snapshot()
    except Exception as exc:
        result["extensions_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return result


def _sanitised_settings() -> dict[str, Any]:
    try:
        from . import settings
        values = settings.all_settings()
    except Exception:
        return {}
    safe = {}
    for key, value in values.items():
        folded = key.lower()
        if any(secret in folded for secret in ("token", "secret", "password", "cookie", "pair")):
            safe[key] = "<redacted>"
        elif key == "journal_dir":
            safe[key] = "<custom>" if value else "<automatic>"
        else:
            safe[key] = value
    return safe


def _redact_local_paths(text: str) -> str:
    """Remove machine/user paths before a diagnostic leaves the app.

    The bundle is only saved locally, but it is specifically intended to be
    attachable to a support request.  Windows paths commonly embed the account
    name, so redact the data root, home directory and any configured journal
    directory from both structured health data and copied log text.
    """
    candidates = {str(marketdb.DATA_DIR), str(Path.home())}
    try:
        from . import settings
        journal_dir = settings.get("journal_dir")
        if journal_dir:
            candidates.add(str(journal_dir))
    except Exception:
        pass
    for candidate in sorted((value for value in candidates if value), key=len, reverse=True):
        text = re.sub(re.escape(candidate), "<local-path>", text, flags=re.IGNORECASE)
        # Tracebacks may use the other slash convention on Windows.
        alternate = candidate.replace("\\", "/") if "\\" in candidate else candidate.replace("/", "\\")
        if alternate != candidate:
            text = re.sub(re.escape(alternate), "<local-path>", text, flags=re.IGNORECASE)
    return _redact_auth_material(text)


def _redact_auth_material(text: str) -> str:
    """Remove bearer, cookie and one-time-link credentials from copied logs."""
    # Request lines and logged URLs: retain the parameter name for diagnosis,
    # but never retain its credential value.
    text = re.sub(
        r"(?i)([?&](?:pair|token|access_token|auth_token|api_key)=)[^&#\s\"']+",
        r"\1<redacted>",
        text,
    )
    # Structured payloads occasionally logged by dependencies.
    text = re.sub(
        r'(?i)([\"\'](?:pair|token|access_token|auth_token|api_key|cookie|authorization)[\"\']\s*:\s*[\"\'])[^\"\']*',
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?im)^(Authorization\s*:\s*)(?:Bearer\s+)?\S+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(r"(?im)^(Cookie\s*:\s*).+$", r"\1<redacted>", text)
    return text


def _sanitised_health() -> dict[str, Any]:
    # A JSON round-trip gives us a detached, serialisable structure before
    # applying the same path scrubber used for logs and exception strings.
    text = json.dumps(health_snapshot(), ensure_ascii=False, default=str)
    return json.loads(_redact_local_paths(text))


def create_bundle() -> Path:
    """Create a bounded support ZIP containing health, settings and logs."""
    configure()
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    with _bundle_lock:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        destination = BUNDLE_DIR / f"frameshift-diagnostics-{stamp}.zip"
        health = json.dumps(_sanitised_health(), indent=2, ensure_ascii=False, default=str)
        settings_text = json.dumps(_sanitised_settings(), indent=2, ensure_ascii=False, default=str)
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("health.json", health)
            bundle.writestr("settings.json", settings_text)
            for path in sorted(LOG_DIR.glob("frameshift.log*")):
                try:
                    if path.is_file() and path.stat().st_size <= 3 * 1024 * 1024:
                        log_text = path.read_text(encoding="utf-8", errors="replace")
                        bundle.writestr(f"logs/{path.name}", _redact_local_paths(log_text))
                except OSError:
                    continue
        bundles = sorted(
            BUNDLE_DIR.glob("frameshift-diagnostics-*.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in bundles[5:]:
            try:
                stale.unlink()
            except OSError:
                pass
        return destination
