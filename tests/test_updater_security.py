"""The updater fails closed and never promotes a partial/unverified binary."""

import errno
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite.updater import (
    BACKUP_SUFFIX,
    MAX_DOWNLOAD_BYTES,
    Updater,
    _base_stem,
    _leftover_names,
    _os_error_detail,
    parse_version,
)


class Response:
    def __init__(self, body=b"", status=200, headers=None, url="https://github.com/example/project/releases/download/v3/asset"):
        self.body = body
        self.status_code = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self.text = body.decode("ascii", errors="replace")
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]


assert parse_version("v2.3.0-beta1") == (2, 3, 0)

with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    binary = b"MZ" + b"verified executable payload"
    digest = hashlib.sha256(binary).hexdigest()
    checksum_asset = {
        "name": "Frameshift.exe.sha256",
        "browser_download_url": "https://github.com/example/project/releases/download/v3/checksum",
    }
    info = {"size": len(binary), "_asset_name": "Frameshift.exe", "_assets": [checksum_asset]}
    path = root / "Frameshift.exe"
    path.write_bytes(binary)

    updater = Updater()
    with patch("elite.updater.requests.get", return_value=Response(digest.encode("ascii"))):
        updater._verify(path, info)

    for broken_info, response in (
        ({**info, "_assets": []}, None),
        (info, Response(b"not-a-digest")),
        (info, Response(("0" * 64).encode("ascii"))),
    ):
        try:
            with patch("elite.updater.requests.get", return_value=response):
                updater._verify(path, broken_info)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unverified release was accepted")

    destination = root / "download.exe"
    with patch("elite.updater.requests.get", return_value=Response(binary)):
        updater._download("https://github.com/example/release.exe", destination)
    assert destination.read_bytes() == binary
    assert not destination.with_suffix(".exe.part").exists()

    truncated = Response(binary, headers={"Content-Length": str(len(binary) + 1)})
    try:
        with patch("elite.updater.requests.get", return_value=truncated):
            updater._download("https://github.com/example/release.exe", destination)
    except RuntimeError:
        pass
    else:
        raise AssertionError("truncated download was accepted")
    assert destination.read_bytes() == binary
    assert not destination.with_suffix(".exe.part").exists()

    oversized = Response(b"", headers={"Content-Length": str(MAX_DOWNLOAD_BYTES + 1)})
    try:
        with patch("elite.updater.requests.get", return_value=oversized):
            updater._download("https://github.com/example/release.exe", destination)
    except RuntimeError:
        pass
    else:
        raise AssertionError("oversized download was accepted")

    # Initial URLs and every final redirect target are independently checked.
    evil_redirect = Response(binary, url="https://attacker.example/payload.exe")
    try:
        with patch("elite.updater.requests.get", return_value=evil_redirect):
            updater._download("https://github.com/example/release.exe", destination)
    except RuntimeError:
        pass
    else:
        raise AssertionError("cross-host download redirect was accepted")

    evil_checksum = Response(digest.encode("ascii"), url="https://attacker.example/hash")
    try:
        with patch("elite.updater.requests.get", return_value=evil_checksum):
            updater._verify(path, info)
    except RuntimeError:
        pass
    else:
        raise AssertionError("cross-host checksum redirect was accepted")

    # Rollback survives the replacement's first healthy launch and is removed
    # only when a later startup reaches the live-server cleanup point.
    class Timer:
        def __init__(self, _delay, function, args=()):
            self.function, self.args, self.daemon, self.name = function, args, False, ""

        def start(self):
            pass

    marker = root / ".frameshift-update-health.json"
    nonce = "a" * 32

    def stage_marker():
        marker.write_text(json.dumps({
            "version": 1, "state": "awaiting_health", "nonce": nonce,
        }), encoding="utf-8")

    rollback = root / f"Frameshift{BACKUP_SUFFIX}"
    rollback.write_bytes(binary)
    # Leftovers from installs that updated before the .rollback.bak scheme,
    # including the .old.old.exe a rollback-copy launch used to strand forever.
    legacy = [root / name for name in (
        "Frameshift.old.exe", "Frameshift.old.old.exe", "EliteTrader.old.exe",
        f"Frameshift{BACKUP_SUFFIX}.tmp",
    )]
    for path in legacy:
        path.write_bytes(binary)
    stage_marker()
    with patch("elite.updater._exe_dir", return_value=root), \
            patch("elite.updater._exe_stem", return_value="Frameshift"), \
            patch("elite.updater.threading.Timer", Timer), \
            patch.object(sys, "frozen", True, create=True):
        updater.cleanup_leftovers()
        assert rollback.is_file(), "first launch discarded the rollback"
        assert all(p.is_file() for p in legacy), "first launch swept leftovers early"
        updater._confirm_healthy_launch(nonce)
        assert json.loads(marker.read_text(encoding="utf-8"))["state"] == "healthy"
        updater.cleanup_leftovers()
        assert not rollback.exists() and not marker.exists()
        for path in legacy:
            assert not path.exists(), f"legacy leftover survived: {path.name}"

    # An install launched from a rollback copy runs as Frameshift.old.exe and
    # keeps that name through every later update. Its backup must normalise back
    # to one file, and cleanup must never aim a delete at the running binary.
    running = root / "Frameshift.old.exe"
    running.write_bytes(binary)
    orphan = root / "Frameshift.old.old.exe"
    orphan.write_bytes(binary)
    stage_marker()
    with patch("elite.updater._exe_dir", return_value=root), \
            patch.object(sys, "executable", str(running)), \
            patch("elite.updater.threading.Timer", Timer), \
            patch.object(sys, "frozen", True, create=True):
        assert _base_stem() == "Frameshift", "the .old chain was not stripped"
        assert f"Frameshift{BACKUP_SUFFIX}" in _leftover_names()
        updater._confirm_healthy_launch(nonce)
        updater.cleanup_leftovers()
        assert running.is_file(), "cleanup deleted the running executable"
        assert not orphan.exists(), "the .old.old.exe orphan survived"

# Update failures name a cause the user can act on. Collapsing every OSError
# into one sentence is what made the Wacatac.B!ml false positive undiagnosable.
class VirusBlocked(OSError):
    winerror = 225


detail = _os_error_detail(VirusBlocked("access denied"))
assert "antivirus" in detail and "225" in detail, detail
assert _os_error_detail(OSError(errno.ENOSPC, "No space left")) == "the disk is full"
assert "locked" in _os_error_detail(PermissionError(13, "Permission denied"))
assert "removed it" in _os_error_detail(FileNotFoundError(2, "No such file"))

print("updater OK: SHA-256 integrity, redirect validation, retained rollback, "
      "leftover sweep, diagnosable failures, atomic downloads")
