"""User settings, persisted to data/settings.json. Environment variables provide
the first-run defaults; once the user changes something in the Settings panel the
saved value wins. Read at runtime so toggles take effect without a restart."""

import json
import os
import threading

from . import marketdb

SETTINGS_PATH = marketdb.DATA_DIR / "settings.json"

# key -> default. Env vars seed the boolean toggles that used to be flags.
DEFAULTS = {
    "eddn_upload": os.environ.get("ET_EDDN_UPLOAD", "1") != "0",
    "auto_update": os.environ.get("ET_AUTO_UPDATE", "1") != "0",
    "exclude_carriers": True,   # keep fleet carriers out of routes/searches
    "exclude_surface": False,   # keep planetary/settlement stations out
    "journal_dir": "",          # manual journal folder; "" = auto-detect
    "pinned_blueprints": [],    # engineering planner pins: [{"name", "grade"}]
    "tts_voice": "en_GB-cori-high",  # neural callout voice (see tts.VOICES)
}

_lock = threading.Lock()
_cache = None


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
        except (OSError, ValueError):
            pass  # missing/corrupt file -> defaults
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
            data[k] = bool(v) if isinstance(DEFAULTS[k], bool) else v
        try:
            marketdb.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
        _cache = data
        return dict(data)
