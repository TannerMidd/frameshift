"""Neural voice callouts: Piper TTS running locally next to the app.

The browser's built-in speechSynthesis speaks with whatever robotic voice the
OS ships. Piper is a small neural TTS that runs fine on CPU and sounds close
to human. Nothing is bundled: a one-time download (~137 MB, pinned URLs,
SHA-256 verified — same pattern as the market database) drops the standalone
piper binary and a British voice model into data/tts. Synthesis happens on
the PC running Elite Trader, so every LAN device hears the same voice.

A single piper process is kept alive in --json-input mode: loading the model
costs ~2s, synthesis itself runs ~4x faster than realtime, and every phrase
is cached as a WAV so repeats are instant.
"""
import atexit
import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from .errors import UserFacingError
from .marketdb import DATA_DIR

TTS_DIR = DATA_DIR / "tts"
CACHE_DIR = TTS_DIR / "cache"
VOICE = "en_GB-cori-high"
MODEL_PATH = TTS_DIR / f"{VOICE}.onnx"
CONFIG_PATH = TTS_DIR / f"{VOICE}.onnx.json"

_PIPER_RELEASE = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2"
_VOICE_BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/cori/high"

# (url, destination, sha256) — everything fetched is pinned.
def _artifacts():
    binary = (
        (f"{_PIPER_RELEASE}/piper_windows_amd64.zip", TTS_DIR / "piper.zip",
         "f3c58906402b24f3a96d92145f58acba6d86c9b5db896d207f78dc80811efcea")
        if sys.platform == "win32" else
        (f"{_PIPER_RELEASE}/piper_linux_x86_64.tar.gz", TTS_DIR / "piper.tar.gz",
         "a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992")
    )
    return [
        binary,
        (f"{_VOICE_BASE}/{VOICE}.onnx", MODEL_PATH,
         "470b4dd634c98f8a4850d7626ffc3dfc90774628eeef6605a6dd8f88f30a5903"),
        (f"{_VOICE_BASE}/{VOICE}.onnx.json", CONFIG_PATH,
         "9e7fb5b5671612c22f3c81cbe46c1ae87b031a4632bcb509e499dad6f1e2adec"),
    ]


def _binary_path():
    exe = "piper.exe" if sys.platform == "win32" else "piper"
    return TTS_DIR / "piper" / exe


class TTSError(UserFacingError):
    pass


def ready():
    return _binary_path().is_file() and MODEL_PATH.is_file() and CONFIG_PATH.is_file()


# ---------- one-time download ----------

_dl_lock = threading.Lock()
_dl = {"running": False, "progress": 0.0, "error": None}


def status():
    with _dl_lock:
        return {
            "ready": ready(),
            "voice": VOICE,
            "downloading": _dl["running"],
            "progress": round(_dl["progress"], 3),
            "error": _dl["error"],
            "supported": sys.platform in ("win32", "linux"),
        }


def start_download():
    if ready():
        return
    if sys.platform not in ("win32", "linux"):
        raise TTSError("The neural voice is only available on Windows and Linux.")
    with _dl_lock:
        if _dl["running"]:
            return
        _dl.update(running=True, progress=0.0, error=None)
    threading.Thread(target=_download, name="tts-download", daemon=True).start()


def _fetch(url, dest, sha256, base, frac):
    """Stream url to dest, verifying the pinned hash; progress in [base, base+frac]."""
    digest = hashlib.sha256()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    with _dl_lock:
                        _dl["progress"] = base + frac * (done / total)
    if digest.hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise TTSError("Downloaded voice files failed verification - try again.")
    tmp.replace(dest)


def _download():
    try:
        TTS_DIR.mkdir(parents=True, exist_ok=True)
        artifacts = _artifacts()
        # The model dominates the bytes; weight the progress bar accordingly.
        weights = (0.15, 0.84, 0.01)
        base = 0.0
        for (url, dest, sha), frac in zip(artifacts, weights):
            if not dest.exists() or dest == artifacts[0][1]:
                _fetch(url, dest, sha, base, frac)
            base += frac
        archive = artifacts[0][1]
        if archive.exists():
            shutil.unpack_archive(str(archive), str(TTS_DIR))
            archive.unlink()
        if not ready():
            raise TTSError("Voice install came out incomplete - delete data/tts and retry.")
        with _dl_lock:
            _dl.update(running=False, progress=1.0, error=None)
    except TTSError as exc:
        with _dl_lock:
            _dl.update(running=False, error=exc.user_message)
    except Exception as exc:
        with _dl_lock:
            _dl.update(running=False, error=f"Download failed ({type(exc).__name__}) - try again.")


# ---------- synthesis ----------

_proc = None
_proc_lock = threading.Lock()
MAX_TEXT = 400
CACHE_KEEP = 300


def _ensure_proc():
    global _proc
    if _proc is not None and _proc.poll() is None:
        return _proc
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    _proc = subprocess.Popen(
        [str(_binary_path()), "--model", str(MODEL_PATH), "--json-input"],
        cwd=str(_binary_path().parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    return _proc


def stop():
    """Terminate the piper process (app shutdown)."""
    global _proc
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            _proc.kill()
        _proc = None


atexit.register(stop)  # piper must not outlive the app (zombie-process lesson)


def _evict_cache():
    wavs = sorted(CACHE_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for p in wavs[:-CACHE_KEEP]:
        try:
            p.unlink()
        except OSError:
            pass


def synthesize(text):
    """Text -> path of a cached WAV. Raises TTSError with a player-facing
    message when the voice isn't installed or piper misbehaves."""
    if not ready():
        raise TTSError("The neural voice isn't installed - download it in Settings.")
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]
    if not text:
        raise TTSError("Nothing to say.")
    key = hashlib.sha1(f"{VOICE}|{text}".encode("utf-8")).hexdigest()
    out = CACHE_DIR / f"{key}.wav"
    if out.is_file() and out.stat().st_size > 44:
        return out
    with _proc_lock:
        if out.is_file() and out.stat().st_size > 44:
            return out
        proc = _ensure_proc()
        try:
            line = json.dumps({"text": text, "output_file": str(out)}) + "\n"
            proc.stdin.write(line.encode("utf-8"))
            proc.stdin.flush()
        except OSError as exc:
            stop()
            raise TTSError("The voice engine stopped - trying again usually fixes it.") from exc
        # Piper writes the WAV when synthesis finishes; wait for it to appear
        # and stop growing. Generous timeout: long phrases on a busy CPU.
        deadline = time.monotonic() + 30
        last_size = -1
        while time.monotonic() < deadline:
            if out.is_file():
                size = out.stat().st_size
                if size > 44 and size == last_size:
                    _evict_cache()
                    return out
                last_size = size
            time.sleep(0.05)
        stop()
        out.unlink(missing_ok=True)
        raise TTSError("The voice engine timed out - trying again usually fixes it.")
