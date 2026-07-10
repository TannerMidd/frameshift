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

_PIPER_RELEASE = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2"
_VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en"

# Curated voice catalog: every model is pinned to a SHA-256 (from the repo's
# LFS pointers), so the download endpoint can only ever fetch these files.
DEFAULT_VOICE = "en_GB-cori-high"
VOICES = {
    "en_GB-cori-high": {
        "label": "Cori — British female", "hf": "en_GB/cori/high", "mb": 109,
        "onnx_sha": "470b4dd634c98f8a4850d7626ffc3dfc90774628eeef6605a6dd8f88f30a5903",
        "config_sha": "9e7fb5b5671612c22f3c81cbe46c1ae87b031a4632bcb509e499dad6f1e2adec",
    },
    "en_GB-alba-medium": {
        "label": "Alba — Scottish female", "hf": "en_GB/alba/medium", "mb": 60,
        "onnx_sha": "401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b",
        "config_sha": "aa965a2f02ecced632c2694e1fc72bbff6d65f265fab567ca945918c73dd89f4",
    },
    "en_GB-northern_english_male-medium": {
        "label": "Northern English male", "hf": "en_GB/northern_english_male/medium", "mb": 60,
        "onnx_sha": "57a219ae8e638873db7d18893304be5069c42868f392bb95c3ff17f0690d0689",
        "config_sha": "69557ed3d974463453e9b0c09dd99a7ed0e52b8b87b64b357dbeeb2540a97d47",
    },
    "en_US-lessac-medium": {
        "label": "Lessac — American female", "hf": "en_US/lessac/medium", "mb": 60,
        "onnx_sha": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        "config_sha": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
    },
    "en_US-ryan-high": {
        "label": "Ryan — American male", "hf": "en_US/ryan/high", "mb": 115,
        "onnx_sha": "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca",
        "config_sha": "c6d3b98f08315cb4bebf0d49d50fc4ff491b503c64b940cd3d5ca28543b48011",
    },
    "en_US-amy-medium": {
        "label": "Amy — American female", "hf": "en_US/amy/medium", "mb": 60,
        "onnx_sha": "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
        "config_sha": "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
    },
}


def active_voice():
    from . import settings  # lazy: avoids an import cycle at module load

    voice = settings.get("tts_voice", DEFAULT_VOICE)
    return voice if voice in VOICES else DEFAULT_VOICE


def model_path(voice):
    return TTS_DIR / f"{voice}.onnx"


def config_path(voice):
    return TTS_DIR / f"{voice}.onnx.json"


def _binary_artifact():
    if sys.platform == "win32":
        return (f"{_PIPER_RELEASE}/piper_windows_amd64.zip", TTS_DIR / "piper.zip",
                "f3c58906402b24f3a96d92145f58acba6d86c9b5db896d207f78dc80811efcea")
    return (f"{_PIPER_RELEASE}/piper_linux_x86_64.tar.gz", TTS_DIR / "piper.tar.gz",
            "a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992")


def _voice_artifacts(voice):
    v = VOICES[voice]
    return [
        (f"{_VOICE_BASE}/{v['hf']}/{voice}.onnx", model_path(voice), v["onnx_sha"]),
        (f"{_VOICE_BASE}/{v['hf']}/{voice}.onnx.json", config_path(voice), v["config_sha"]),
    ]


def _binary_path():
    exe = "piper.exe" if sys.platform == "win32" else "piper"
    return TTS_DIR / "piper" / exe


class TTSError(UserFacingError):
    pass


def voice_installed(voice):
    return model_path(voice).is_file() and config_path(voice).is_file()


def ready():
    return _binary_path().is_file() and voice_installed(active_voice())


# ---------- one-time download ----------

_dl_lock = threading.Lock()
_dl = {"running": False, "progress": 0.0, "error": None}


def status():
    with _dl_lock:
        return {
            "ready": ready(),
            "voice": active_voice(),
            "voices": [
                {"name": name, "label": v["label"], "mb": v["mb"], "installed": voice_installed(name)}
                for name, v in VOICES.items()
            ],
            "downloading": _dl["running"],
            "progress": round(_dl["progress"], 3),
            "error": _dl["error"],
            "supported": sys.platform in ("win32", "linux"),
        }


def set_voice(name):
    """Switch the callout voice (downloading it first if needed). The change
    is server-wide: synthesis happens here, every device hears the result."""
    from . import settings

    if name not in VOICES:
        raise TTSError("Unknown voice.")
    settings.update({"tts_voice": name})
    stop()  # next synthesis restarts piper with the new model
    if not voice_installed(name):
        start_download()


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
        voice = active_voice()
        artifacts = []
        if not _binary_path().is_file():
            artifacts.append(_binary_artifact())
        artifacts += [a for a in _voice_artifacts(voice) if not a[1].is_file()]
        # Rough progress weighting: the .onnx model dominates the bytes.
        weights = [
            0.84 if dest.suffix == ".onnx" else (0.15 if "piper" in dest.name else 0.01)
            for _, dest, _ in artifacts
        ]
        scale = sum(weights) or 1
        base = 0.0
        for (url, dest, sha), w in zip(artifacts, weights):
            _fetch(url, dest, sha, base, w / scale)
            base += w / scale
        archive = _binary_artifact()[1]
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
_proc_voice = None
_proc_lock = threading.Lock()
MAX_TEXT = 400
CACHE_KEEP = 300


def _ensure_proc(voice):
    global _proc, _proc_voice
    if _proc is not None and _proc.poll() is None and _proc_voice == voice:
        return _proc
    if _proc is not None and _proc.poll() is None:
        _proc.kill()  # voice changed: reload with the new model
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    _proc = subprocess.Popen(
        [str(_binary_path()), "--model", str(model_path(voice)), "--json-input"],
        cwd=str(_binary_path().parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    _proc_voice = voice
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
    voice = active_voice()
    if not ready():
        raise TTSError("The neural voice isn't installed - download it in Settings.")
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]
    if not text:
        raise TTSError("Nothing to say.")
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    out = CACHE_DIR / f"{key}.wav"
    if out.is_file() and out.stat().st_size > 44:
        return out
    with _proc_lock:
        if out.is_file() and out.stat().st_size > 44:
            return out
        proc = _ensure_proc(voice)
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
