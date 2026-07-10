"""Neural voice (Piper): pinned artifacts, error paths, endpoint wiring, and —
when the voice is installed on this machine — a real synthesis round-trip."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from elite import tts
from elite.errors import UserFacingError
from elite.server import create_app
from elite.state import AppState

# Every downloadable artifact — the binary and every catalog voice — is
# pinned to a SHA-256, so the endpoints can only ever fetch these files.
artifacts = [tts._binary_artifact()]
for name in tts.VOICES:
    artifacts += tts._voice_artifacts(name)
for url, dest, sha in artifacts:
    assert url.startswith("https://"), url
    assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), (url, sha)
assert issubclass(tts.TTSError, UserFacingError)
assert tts.DEFAULT_VOICE in tts.VOICES
assert tts.active_voice() in tts.VOICES

# Unknown voices are rejected before touching settings or the network.
try:
    tts.set_voice("evil-voice-name")
    raise AssertionError("expected TTSError")
except tts.TTSError:
    pass

# Not installed -> player-facing error, no crash.
real_ready = tts.ready
tts.ready = lambda: False
try:
    try:
        tts.synthesize("hello")
        raise AssertionError("expected TTSError")
    except tts.TTSError as exc:
        assert "Settings" in exc.user_message

    app = create_app(AppState())
    app.testing = True
    client = app.test_client()
    resp = client.get("/api/speak?text=hello")
    assert resp.status_code == 409 and "Settings" in resp.get_json()["error"], resp.get_json()
    resp = client.get("/api/tts/status")
    assert resp.status_code == 200 and resp.get_json()["ready"] is False
finally:
    tts.ready = real_ready

print("tts guards OK: pinned artifacts, user-facing errors, endpoint wiring")

# Real synthesis when the voice is installed here (skipped elsewhere).
if tts.ready():
    wav = tts.synthesize("Fuel scoop test. o7")
    data = wav.read_bytes()
    assert data[:4] == b"RIFF" and len(data) > 10000, (wav, len(data))
    wav2 = tts.synthesize("  Fuel   scoop\n test.  o7 ")  # whitespace-normalized -> cache hit
    assert wav2 == wav
    tts.stop()
    print(f"tts synthesis OK: {len(data):,} byte WAV, cache hit on normalized repeat")
else:
    print("tts synthesis SKIPPED: voice not installed on this machine")
