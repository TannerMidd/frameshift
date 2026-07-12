"""In-app auto-update for the packaged Windows exe.

Checks the GitHub Releases API for a newer exe, downloads and verifies it,
then hands off to a tiny batch script that waits for this process to exit,
swaps the exe in place, and relaunches. Only active in the frozen onefile
build on Windows; a no-op for source/headless runs.

Rename continuity (Elite Trader -> Frameshift, v2.0.0): installs from before
the rename hit the old repo URL (GitHub 301-redirects it here) and look for an
asset named exactly "EliteTrader.exe", so every release publishes the same
binary under both names. This module prefers the new asset name but falls back
to the legacy one, and derives all staging/backup filenames from the *running*
exe's own name — an updated install keeps whatever filename it has on disk.

Distribution is already GitHub-Releases based (see .github/workflows/release.yml),
so no extra infrastructure is needed."""

import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

try:
    from ._version import VERSION
except Exception:  # pragma: no cover - _version is always present in practice
    VERSION = "0.0.0"

REPO = os.environ.get("ET_UPDATE_REPO", "TannerMidd/frameshift")
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# Newest name first; the legacy name keeps pre-rename releases installable.
ASSET_NAMES = ("Frameshift.exe", "EliteTrader.exe")
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "Frameshift-Updater"}
CHECK_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
HEALTH_CONFIRM_SECONDS = 60
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_logger = logging.getLogger(__name__)
_DOWNLOAD_HOSTS = {
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
}
_CHECKSUM_HOSTS = _DOWNLOAD_HOSTS
_API_HOSTS = {"api.github.com"}


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
        match = re.match(r"\d+", part)
        if not match:
            break
        nums.append(int(match.group(0)))
    return tuple(nums) or (0,)


def _exe_dir():
    return Path(sys.executable).resolve().parent


def _exe_stem():
    """Filename stem of the running exe. Installs updated across the rename
    still live at EliteTrader.exe (the in-place swap keeps the old filename),
    so staging/backup names must follow the actual file, not the product name."""
    return Path(sys.executable).stem


def _trusted_https(url, hosts):
    try:
        parsed = urlsplit(str(url or ""))
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() in hosts
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


def _require_final_url(response, hosts, label):
    final_url = getattr(response, "url", None)
    if not _trusted_https(final_url, hosts):
        raise RuntimeError(f"{label} redirected to an untrusted address")


def _update_health_path():
    return _exe_dir() / ".frameshift-update-health.json"


def _write_update_health(payload):
    path = _update_health_path()
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _read_update_health():
    try:
        path = _update_health_path()
        if path.stat().st_size > 16 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def updater_script(exe_path, new_path):
    """The helper batch that installs a downloaded update.

    Two things make the relaunch reliable:

    1. The move is *retried* until it succeeds. On Windows you cannot overwrite a
       running exe, so `move /y` only lands once our process has fully exited and
       released the lock — the retry loop is therefore an implicit, race-free
       "wait until the old app is really gone" gate (no need to poll for the PID).
       `ping` provides the small inter-try delay because `timeout` needs a console
       this windowless helper process does not have.

    2. The PyInstaller onefile bootloader vars (`_MEIPASS2` et al.) are cleared
       before relaunch. A onefile exe is a bootloader that, on launch, sets
       `_MEIPASS2` to its temp extraction dir and hands off to a child. If the
       relaunched exe *inherits* `_MEIPASS2` from our dying process, its bootloader
       skips extraction and tries to load python312.dll from the OLD (now-deleted)
       `_MEIxxxxx` dir — the "Failed to load Python DLL" failure. Double-clicking
       works precisely because Explorer launches with a clean environment; this is
       the real cause the earlier settle-delay never fixed. We pass a scrubbed
       environment from Python (see _stage_and_restart); clearing them here too is
       belt-and-suspenders."""
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        # Belt-and-suspenders: ensure the relaunched exe never inherits the
        # onefile bootloader hand-off vars (see docstring).
        'set "_MEIPASS2="\r\n'
        'set "_MEIPASS="\r\n'
        'set "_PYI_ARCHIVE_INDEX="\r\n'
        'set "_PYI_APPLICATION_HOME_DIR="\r\n'
        "set n=0\r\n"
        ":retry\r\n"
        f'move /y "{new_path}" "{exe_path}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto launch\r\n"
        "set /a n+=1\r\n"
        "if %n% geq 120 goto cleanup\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto retry\r\n"
        ":launch\r\n"
        # Small settle margin (freshly-written exe; lets any AV on-write scan
        # finish). Not the fix — the clean environment above is — just courtesy.
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'start "" "{exe_path}"\r\n'
        ":cleanup\r\n"
        'del "%~f0" >nul 2>&1\r\n'
    )


def _clean_child_env():
    """A copy of the current environment with the PyInstaller onefile bootloader
    variables removed, so a relaunched onefile exe extracts fresh instead of
    inheriting our dying process's temp dir. Stripping the whole `_MEI*` / `_PYI*`
    namespace covers current and future bootloader vars; nothing legitimate uses
    those prefixes."""
    return {
        k: v for k, v in os.environ.items()
        if not (k.startswith("_MEI") or k.startswith("_PYI"))
    }


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
        self._health_timer = None

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
            "notes": None,        # release body (markdown) for the in-app viewer
            "notes_title": None,
            "size": None,
            "supported": is_supported(),
            # The digest is delivered beside the executable by the same GitHub
            # release. It detects corruption/truncation; it is not an
            # independent publisher signature.
            "verification": "SHA-256 integrity (same release channel)",
            "error": None,
        }
        try:
            resp = requests.get(
                API_LATEST, headers=HEADERS, timeout=CHECK_TIMEOUT,
                allow_redirects=True,
            )
            _require_final_url(resp, _API_HOSTS, "release metadata")
            if resp.status_code != 200:
                result["error"] = f"GitHub returned {resp.status_code}"
            else:
                data = resp.json()
                tag = data.get("tag_name") or ""
                assets = data.get("assets") or []
                asset = next((a for name in ASSET_NAMES
                              for a in assets if a.get("name") == name), None)
                result["latest"] = tag.lstrip("vV") or None
                result["notes_url"] = data.get("html_url") or result["notes_url"]
                result["notes"] = data.get("body") or None
                result["notes_title"] = data.get("name") or tag or None
                if asset:
                    result["size"] = asset.get("size")
                    result["_download_url"] = asset.get("browser_download_url")
                    result["_asset_name"] = asset.get("name")
                    result["_assets"] = assets
                result["available"] = bool(asset) and \
                    parse_version(tag) > parse_version(VERSION)
        except (requests.RequestException, RuntimeError) as exc:
            # Only the exception class name: check() results end up in API
            # responses, and full requests error text carries internal detail.
            result["error"] = f"Could not reach GitHub ({type(exc).__name__})"

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
        new_path = _exe_dir() / f"{_exe_stem()}.new.exe"
        try:
            self._download(info["_download_url"], new_path)
            self._set(phase="verifying")
            self._verify(new_path, info)
            self._set(phase="restarting")
            time.sleep(0.4)  # let the client see "restarting" / poll once more
            self._stage_and_restart(new_path)
        except Exception as exc:
            for path in (new_path, new_path.with_suffix(new_path.suffix + ".part")):
                try:
                    path.unlink()
                except OSError:
                    pass
            _logger.warning("Update failed during %s: %s", self.phase, type(exc).__name__)
            self._set(phase="error", error=str(exc))

    def _download(self, url, dest):
        if not _trusted_https(url, _DOWNLOAD_HOSTS):
            raise RuntimeError("release download URL is not a trusted HTTPS GitHub address")
        partial = dest.with_suffix(dest.suffix + ".part")
        try:
            partial.unlink()
        except OSError:
            pass
        written = 0
        try:
            with requests.get(
                url, headers=HEADERS, stream=True, timeout=DOWNLOAD_TIMEOUT,
                allow_redirects=True,
            ) as r:
                _require_final_url(r, _DOWNLOAD_HOSTS, "release download")
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                if total < 0 or total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("release download is unexpectedly large")
                if total:
                    self._set(total=total)
                with open(partial, "xb") as f:
                    for chunk in r.iter_content(chunk_size=262144):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("release download exceeded the safety limit")
                        f.write(chunk)
                        with self._lock:
                            self.downloaded += len(chunk)
                    f.flush()
                    os.fsync(f.fileno())
                if total and written != total:
                    raise RuntimeError(f"release download was truncated ({written} != {total})")
            os.replace(partial, dest)
        except Exception:
            try:
                partial.unlink()
            except OSError:
                pass
            raise

    def _verify(self, path, info):
        size = path.stat().st_size
        if info.get("size") and size != info["size"]:
            raise RuntimeError(f"download size mismatch ({size} != {info['size']})")
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                raise RuntimeError("downloaded file is not a Windows executable")
        # Checksums are mandatory. A missing or unreachable digest is not an
        # excuse to install unverifiable executable code.
        expected = self._expected_sha256(info)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest().lower() != expected.lower():
            raise RuntimeError("checksum mismatch — refusing to install")

    def _expected_sha256(self, info):
        want = (info.get("_asset_name") or ASSET_NAMES[0]) + ".sha256"
        asset = next((a for a in info.get("_assets") or []
                      if a.get("name") == want), None)
        if not asset:
            raise RuntimeError("release checksum is missing — refusing to install")
        url = asset.get("browser_download_url")
        if not _trusted_https(url, _CHECKSUM_HOSTS):
            raise RuntimeError("release checksum URL is not trusted")
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=CHECK_TIMEOUT,
                allow_redirects=True,
            )
            _require_final_url(resp, _CHECKSUM_HOSTS, "release checksum")
            resp.raise_for_status()
        except (requests.RequestException, RuntimeError) as exc:
            raise RuntimeError("release checksum could not be downloaded — refusing to install") from exc
        token = (resp.text or "").strip().split()[0] if (resp.text or "").strip() else ""
        if not _SHA256_RE.fullmatch(token):
            raise RuntimeError("release checksum is malformed — refusing to install")
        return token.lower()

    def _stage_and_restart(self, new_path):
        """Write a helper batch that swaps in the new exe and relaunches, then
        quit so it can do its work."""
        exe = Path(sys.executable).resolve()
        # Back up the current, working exe so a bad launch of the new one is
        # recoverable: if the new exe fails to start, cleanup_leftovers never
        # runs, so the .old.exe survives for the user to rename back.
        # Reading a running exe is allowed on Windows.
        import shutil

        backup = _exe_dir() / f"{_exe_stem()}.old.exe"
        backup_temp = backup.with_suffix(backup.suffix + ".tmp")
        try:
            shutil.copy2(exe, backup_temp)
            with open(backup_temp, "rb") as stream:
                if stream.read(2) != b"MZ":
                    raise OSError("backup is not a Windows executable")
            os.replace(backup_temp, backup)
        except OSError as exc:
            try:
                backup_temp.unlink()
            except OSError:
                pass
            raise RuntimeError("could not create a rollback copy — update cancelled") from exc
        try:
            _write_update_health({
                "version": 1,
                "state": "awaiting_health",
                "nonce": secrets.token_hex(16),
                "staged_at": int(time.time()),
                "executable": exe.name,
            })
        except OSError as exc:
            try:
                backup.unlink()
            except OSError:
                pass
            raise RuntimeError("could not create the rollback health marker") from exc
        bat = _exe_dir() / "_et_update.bat"
        bat.write_text(updater_script(exe, new_path), encoding="ascii")
        # CREATE_NO_WINDOW (not DETACHED_PROCESS) keeps a console so the batch's
        # delays work, but shows no window; the new group lets it outlive us.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) \
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # Pass a scrubbed environment so cmd -> batch -> the relaunched exe never
        # inherit `_MEIPASS2` (the onefile relaunch bug — see updater_script).
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags,
                         close_fds=True, env=_clean_child_env())
        time.sleep(0.3)  # let the helper start, then exit so the exe unlocks
        os._exit(0)

    def cleanup_leftovers(self):
        """Advance rollback health only after a live server has started.

        The first replacement launch keeps the old executable and must remain
        alive for a sustained window before its marker becomes healthy. Only a
        later successful startup removes that rollback copy. Crashes and
        malformed markers fail closed by retaining the known-good executable.
        """
        if not getattr(sys, "frozen", False):
            return
        try:
            (_exe_dir() / "_et_update.bat").unlink()
        except OSError:
            pass
        marker = _read_update_health()
        if not marker:
            return
        if marker.get("state") == "awaiting_health":
            nonce = marker.get("nonce")
            if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
                return
            timer = threading.Timer(
                HEALTH_CONFIRM_SECONDS, self._confirm_healthy_launch, args=(nonce,)
            )
            timer.daemon = True
            timer.name = "update-health-confirm"
            timer.start()
            self._health_timer = timer
            return
        if marker.get("state") != "healthy":
            return
        for name in (f"{_exe_stem()}.old.exe", "EliteTrader.old.exe", "Frameshift.old.exe"):
            try:
                (_exe_dir() / name).unlink()
            except OSError:
                pass
        try:
            _update_health_path().unlink()
        except OSError:
            pass

    def _confirm_healthy_launch(self, nonce):
        marker = _read_update_health()
        if not marker or marker.get("state") != "awaiting_health" or marker.get("nonce") != nonce:
            return
        marker["state"] = "healthy"
        marker["healthy_at"] = int(time.time())
        try:
            _write_update_health(marker)
        except OSError:
            # Retaining the rollback copy is the safe failure mode.
            return


UPDATER = Updater()
