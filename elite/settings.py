"""User settings, persisted to data/settings.json. Environment variables provide
the first-run defaults; once the user changes something in the Settings panel the
saved value wins. Read at runtime so toggles take effect without a restart."""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from . import marketdb
from .errors import UserFacingError

SETTINGS_PATH = marketdb.DATA_DIR / "settings.json"

# key -> default. Env vars seed the boolean toggles that used to be flags.
DEFAULTS = {
    "eddn_upload": os.environ.get("ET_EDDN_UPLOAD", "1") != "0",
    # Broader journal/snapshot contribution is intentionally a separate,
    # informed opt-in.  The long-standing anonymous commodity contribution
    # remains enabled by default; exploration, route, docking and station
    # inventory observations do not silently inherit that consent.
    "eddn_extended_upload": os.environ.get("ET_EDDN_EXTENDED_UPLOAD", "0") == "1",
    "auto_update": os.environ.get("ET_AUTO_UPDATE", "1") != "0",
    "exclude_carriers": True,   # keep fleet carriers out of routes/searches
    "exclude_surface": False,   # keep planetary/settlement stations out
    "journal_dir": "",          # manual journal folder; "" = auto-detect
    "pinned_blueprints": [],    # engineering planner pins: [{"name", "grade"}]
    "tts_voice": "en_GB-cori-high",  # neural callout voice (see tts.VOICES)
}

_lock = threading.Lock()
_cache = None
_logger = logging.getLogger(__name__)


class SettingsError(UserFacingError):
    """A settings change could not be safely persisted."""


def _backup_corrupt(path: Path) -> None:
    """Move malformed settings aside instead of destroying the evidence."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, backup)
        _logger.warning("Moved corrupt settings file to %s", backup.name)
    except OSError as exc:
        _logger.warning("Could not preserve corrupt settings file: %s", type(exc).__name__)


def _normalise(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{key} must be true or false.")
        return value
    if isinstance(default, list):
        if not isinstance(value, list):
            raise SettingsError(f"{key} must be a list.")
        # Settings are small UI preferences, never an unbounded storage API.
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise SettingsError(f"{key} is too large.")
        return value
    if isinstance(default, str):
        if not isinstance(value, str):
            raise SettingsError(f"{key} must be text.")
        if len(value) > 4096:
            raise SettingsError(f"{key} is too long.")
        return value
    return value


def _atomic_write(data):
    parent = SETTINGS_PATH.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp = parent / f".{SETTINGS_PATH.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp, "x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, SETTINGS_PATH)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        raise SettingsError("Settings could not be saved. Your previous settings are unchanged.") from exc


def _load_locked():
    global _cache
    if _cache is None:
        data = dict(DEFAULTS)
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in DEFAULTS:
                if k in saved:
                    data[k] = saved[k]
        except ValueError:
            _backup_corrupt(SETTINGS_PATH)
        except OSError:
            pass  # first run / temporarily unavailable -> defaults
        _cache = data
    return _cache


def get(key, default=None):
    with _lock:
        return _load_locked().get(key, DEFAULTS.get(key, default))


def all_settings():
    with _lock:
        return dict(_load_locked())


def update(changes):
    """Apply and persist a partial dict of changes; unknown keys are ignored."""
    global _cache
    with _lock:
        data = dict(_load_locked())
        for k, v in (changes or {}).items():
            if k not in DEFAULTS:
                continue
            data[k] = _normalise(k, v)
        _atomic_write(data)
        _cache = data
        return dict(data)
