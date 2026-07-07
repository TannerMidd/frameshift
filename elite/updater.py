"""In-app auto-update for the packaged Windows exe.

Checks the GitHub Releases API for a newer EliteTrader.exe, downloads and
verifies it, then hands off to a tiny batch script that waits for this process
to exit, swaps the exe in place, and relaunches. Only active in the frozen
onefile build on Windows; a no-op for source/headless runs.

Distribution is already GitHub-Releases based (see .github/workflows/release.yml),
so no extra infrastructure is needed."""

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

try:
    from ._version import VERSION
except Exception:  # pragma: no cover - _version is always present in practice
    VERSION = "0.0.0"

REPO = os.environ.get("ET_UPDATE_REPO", "TannerMidd/elite-trader")
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "EliteTrader.exe"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "EliteTrader-Updater"}
CHECK_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60


def is_supported():
    """Auto-update only makes sense for the packaged Windows exe."""
    from . import settings

    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32" \
        and bool(settings.get("auto_update", True))


def parse_version(text):
    """'v1.2.0' / '1.2.0' -> (1, 2, 0). Non-numeric parts are ignored so a
    pre-release like '1.3.0-beta' still compares sensibly on its numbers."""
    nums = []
    for part in str(text or "").strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        if not digits:
            break
        nums.append(int(digits))
    return tuple(nums) or (0,)


def _exe_dir():
    return Path(sys.executable).resolve().parent


def updater_script(exe_path, new_path):
    """The helper batch that installs a downloaded update.

    PyInstaller's onefile app is a bootloader parent plus a child, and both hold
    EliteTrader.exe locked for a moment after the app exits — so the move is
    retried until the file is actually free rather than attempted a fixed number
    of times. `ping` provides the delay because `timeout` needs a console this
    windowless helper process does not have (the original bug: the app closed but
    never swapped or relaunched, leaving EliteTrader.new.exe behind)."""
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        "set n=0\r\n"
        ":retry\r\n"
        f'move /y "{new_path}" "{exe_path}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto launch\r\n"
        "set /a n+=1\r\n"
        "if %n% geq 120 goto cleanup\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto retry\r\n"
        ":launch\r\n"
        f'start "" "{exe_path}"\r\n'
        ":cleanup\r\n"
        'del "%~f0" >nul 2>&1\r\n'
    )


class Updater:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._latest = None          # cached GitHub check result
        self._checked_at = 0
        self.phase = "idle"          # idle | downloading | verifying | restarting | error
        self.error = None
        self.downloaded = 0
        self.total = 0

    # ---------- version / check ----------

    def current_version(self):
        return VERSION

    def check(self, force=False, ttl=1800):
        """Return {current, latest, available, notes_url, size, supported, error}.
        Result is cached for `ttl` seconds so the UI can poll cheaply."""
        now = time.time()
        with self._lock:
            cached, checked_at = self._latest, self._checked_at
        if cached and not force and (now - checked_at) < ttl:
            return cached

        result = {
            "current": VERSION,
            "latest": None,
            "available": False,
            "notes_url": f"https://github.com/{REPO}/releases/latest",
            "size": None,
            "supported": is_supported(),
            "error": None,
        }
        try:
            resp = requests.get(API_LATEST, headers=HEADERS, timeout=CHECK_TIMEOUT)
            if resp.status_code != 200:
                result["error"] = f"GitHub returned {resp.status_code}"
            else:
                data = resp.json()
                tag = data.get("tag_name") or ""
                asset = next((a for a in data.get("assets") or []
                              if a.get("name") == ASSET_NAME), None)
                result["latest"] = tag.lstrip("vV") or None
                result["notes_url"] = data.get("html_url") or result["notes_url"]
                if asset:
                    result["size"] = asset.get("size")
                    result["_download_url"] = asset.get("browser_download_url")
                    result["_assets"] = data.get("assets") or []
                result["available"] = bool(asset) and \
                    parse_version(tag) > parse_version(VERSION)
        except requests.RequestException as exc:
            result["error"] = f"Could not reach GitHub: {exc}"

        with self._lock:
            # Only cache a good result; a failed/errored check must not stick
            # around for the whole TTL, or a transient hiccup at launch hides
            # updates until the next long re-poll.
            if not result.get("error"):
                self._latest = result
                self._checked_at = now
        return result

    # ---------- update flow ----------

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start_update(self):
        """Kick off download + verify + restart in the background. Returns
        (ok, error)."""
        if not is_supported():
            return False, "Auto-update is only available in the packaged Windows app."
        if self.running():
            return False, "An update is already in progress."
        info = self.check(force=True)
        if info.get("error"):
            return False, info["error"]
        if not info.get("available"):
            return False, "You're already on the latest version."
        if not info.get("_download_url"):
            return False, "The latest release has no downloadable exe."
        with self._lock:
            self.phase = "downloading"
            self.error = None
            self.downloaded = 0
            self.total = info.get("size") or 0
        self._thread = threading.Thread(
            target=self._run, args=(info,), name="updater", daemon=True
        )
        self._thread.start()
        return True, None

    def progress(self):
        with self._lock:
            pct = round(100 * self.downloaded / self.total) if self.total else 0
            return {
                "phase": self.phase,
                "error": self.error,
                "downloaded_mb": round(self.downloaded / 1e6, 1),
                "total_mb": round(self.total / 1e6, 1),
                "pct": pct,
            }

    def _set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _run(self, info):
        try:
            new_path = _exe_dir() / "EliteTrader.new.exe"
            self._download(info["_download_url"], new_path)
            self._set(phase="verifying")
            self._verify(new_path, info)
            self._set(phase="restarting")
            time.sleep(0.4)  # let the client see "restarting" / poll once more
            self._stage_and_restart(new_path)
        except Exception as exc:
            self._set(phase="error", error=str(exc))

    def _download(self, url, dest):
        with requests.get(url, headers=HEADERS, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            if total:
                self._set(total=total)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
                        with self._lock:
                            self.downloaded += len(chunk)

    def _verify(self, path, info):
        size = path.stat().st_size
        if info.get("size") and size != info["size"]:
            raise RuntimeError(f"download size mismatch ({size} != {info['size']})")
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                raise RuntimeError("downloaded file is not a Windows executable")
        # If the release publishes a checksum asset, verify it.
        expected = self._expected_sha256(info)
        if expected:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            if h.hexdigest().lower() != expected.lower():
                raise RuntimeError("checksum mismatch — refusing to install")

    def _expected_sha256(self, info):
        asset = next((a for a in info.get("_assets") or []
                      if a.get("name") == ASSET_NAME + ".sha256"), None)
        if not asset:
            return None
        try:
            resp = requests.get(asset["browser_download_url"], headers=HEADERS, timeout=CHECK_TIMEOUT)
            if resp.status_code == 200:
                return resp.text.strip().split()[0]
        except requests.RequestException:
            pass
        return None

    def _stage_and_restart(self, new_path):
        """Write a helper batch that swaps in the new exe and relaunches, then
        quit so it can do its work."""
        exe = Path(sys.executable).resolve()
        bat = _exe_dir() / "_et_update.bat"
        bat.write_text(updater_script(exe, new_path), encoding="ascii")
        # CREATE_NO_WINDOW (not DETACHED_PROCESS) keeps a console so the batch's
        # delays work, but shows no window; the new group lets it outlive us.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) \
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, close_fds=True)
        time.sleep(0.3)  # let the helper start, then exit so the exe unlocks
        os._exit(0)

    def cleanup_leftovers(self):
        """Remove stale staging artifacts on launch. Never deletes
        EliteTrader.new.exe — that may be a downloaded update not yet applied,
        which the next update run overwrites anyway."""
        if not getattr(sys, "frozen", False):
            return
        for name in ("_et_update.bat", "EliteTrader.old.exe"):
            try:
                (_exe_dir() / name).unlink()
            except OSError:
                pass


UPDATER = Updater()
