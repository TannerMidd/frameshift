"""Settings persistence is atomic, bounded, validated, and recoverable."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite import settings


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    original_path, original_cache = settings.SETTINGS_PATH, settings._cache
    settings.SETTINGS_PATH = root / "settings.json"
    settings._cache = None
    try:
        assert settings.get("eddn_extended_upload") is False
        saved = settings.update({"auto_update": False, "journal_dir": "X:/Journals"})
        assert saved["auto_update"] is False
        assert json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))["journal_dir"] == "X:/Journals"
        assert not list(root.glob("*.tmp")) and not list(root.glob(".*.tmp"))

        previous = settings.SETTINGS_PATH.read_bytes()
        with patch.object(settings.os, "replace", side_effect=OSError("disk full")):
            try:
                settings.update({"auto_update": True})
            except settings.SettingsError:
                pass
            else:
                raise AssertionError("write failure was hidden")
        assert settings.SETTINGS_PATH.read_bytes() == previous
        assert settings.get("auto_update") is False, "failed write changed in-memory settings"

        for bad in ("false", 0, None):
            try:
                settings.update({"auto_update": bad})
            except settings.SettingsError:
                pass
            else:
                raise AssertionError(f"accepted invalid boolean {bad!r}")

        settings.SETTINGS_PATH.write_text("{broken", encoding="utf-8")
        settings._cache = None
        assert settings.get("auto_update") is settings.DEFAULTS["auto_update"]
        assert list(root.glob("settings.json.corrupt-*"))
    finally:
        settings.SETTINGS_PATH, settings._cache = original_path, original_cache

print("settings OK: atomic writes, recovery backup, strict types, failure rollback")
