"""The updater fails closed and never promotes a partial/unverified binary."""

import errno
import base64
import hashlib
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite.updater import (
    API_LATEST,
    BACKUP_SUFFIX,
    MANIFEST_NAME,
    MAX_DOWNLOAD_BYTES,
    SIGNATURE_NAME,
    Updater,
    _base_stem,
    _leftover_names,
    _os_error_detail,
    parse_version,
)
from elite.update_trust import UpdateSignatureError


class Response:
    def __init__(self, body=b"", status=200, headers=None,
                 url="https://github.com/example/project/releases/download/v3/asset",
                 json_data=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self.text = body.decode("ascii", errors="replace")
        self.url = url
        self.json_data = json_data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.json_data

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]


assert parse_version("v2.3.0-beta1") == (2, 3, 0)

with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    binary = b"MZ" + b"verified executable payload"
    digest = hashlib.sha256(binary).hexdigest()
    signing_key = Ed25519PrivateKey.generate()
    public_key = signing_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    public_key_b64 = base64.b64encode(public_key).decode("ascii")
    manifest_url = "https://github.com/example/project/releases/download/v3/Frameshift-update.json"
    signature_url = manifest_url + ".sig"

    def signed_payload(*, wanted_digest=digest, version="3.0.0"):
        manifest = {
            "schema": 1,
            "version": version,
            "assets": {
                "Frameshift.exe": {"sha256": wanted_digest, "size": len(binary)},
            },
        }
        payload = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        signature = base64.b64encode(signing_key.sign(payload)) + b"\n"
        return payload, signature

    def release_info(assets=True):
        release_assets = []
        if assets:
            release_assets = [
                {"name": MANIFEST_NAME, "browser_download_url": manifest_url},
                {"name": SIGNATURE_NAME, "browser_download_url": signature_url},
            ]
        return {
            "latest": "3.0.0",
            "size": len(binary),
            "_asset_name": "Frameshift.exe",
            "_assets": release_assets,
        }

    def asset_responses(payload, signature, *, evil_manifest_redirect=False):
        def get(url, **_kwargs):
            if url == manifest_url:
                final = "https://attacker.example/manifest" if evil_manifest_redirect else url
                return Response(payload, url=final)
            if url == signature_url:
                return Response(signature, url=url)
            raise AssertionError(f"unexpected release asset URL: {url}")
        return get

    info = release_info()
    path = root / "Frameshift.exe"
    path.write_bytes(binary)

    updater = Updater()
    good_manifest, good_signature = signed_payload()
    with patch("elite.update_trust.PUBLIC_KEY_B64", public_key_b64), \
            patch("elite.updater.requests.get",
                  side_effect=asset_responses(good_manifest, good_signature)):
        updater._verify(path, info)

    release_metadata = {
        "tag_name": "v3.0.0",
        "html_url": "https://github.com/example/project/releases/tag/v3.0.0",
        "name": "Frameshift 3.0.0",
        "body": "Signed release",
        "assets": [
            {
                "name": "Frameshift.exe",
                "size": len(binary),
                "browser_download_url": (
                    "https://github.com/example/project/releases/download/v3/Frameshift.exe"
                ),
            },
            *release_info()["_assets"],
        ],
    }

    def check_responses(payload, signature):
        release_asset = asset_responses(payload, signature)

        def get(url, **kwargs):
            if url == API_LATEST:
                return Response(json_data=release_metadata, url=API_LATEST)
            return release_asset(url, **kwargs)
        return get

    checked = Updater()
    with patch("elite.update_trust.PUBLIC_KEY_B64", public_key_b64), \
            patch("elite.updater.requests.get",
                  side_effect=check_responses(good_manifest, good_signature)):
        checked_info = checked.check(force=True)
    assert checked_info["available"] is True
    assert checked_info["error"] is None
    assert checked_info["verification"].startswith("Ed25519")

    rejected = Updater()
    with patch("elite.update_trust.PUBLIC_KEY_B64", public_key_b64), \
            patch("elite.updater.requests.get",
                  side_effect=check_responses(good_manifest, b"bad")):
        rejected_info = rejected.check(force=True)
    assert rejected_info["available"] is False
    assert "signature" in rejected_info["error"].lower()

    malformed_metadata = {**release_metadata, "tag_name": ["v3.0.0"]}
    malformed = Updater()
    with patch("elite.updater.requests.get", return_value=Response(
        json_data=malformed_metadata, url=API_LATEST
    )):
        malformed_info = malformed.check(force=True)
    assert malformed_info["available"] is False
    assert malformed_info["error"], "malformed metadata escaped as an exception"

    wrong_digest_manifest, wrong_digest_signature = signed_payload(wanted_digest="0" * 64)
    for broken_info, payload, signature in (
        (release_info(assets=False), good_manifest, good_signature),
        (release_info(), good_manifest + b" ", good_signature),
        (release_info(), good_manifest, b"not-a-signature"),
        (release_info(), wrong_digest_manifest, wrong_digest_signature),
    ):
        try:
            with patch("elite.update_trust.PUBLIC_KEY_B64", public_key_b64), \
                    patch("elite.updater.requests.get",
                          side_effect=asset_responses(payload, signature)):
                updater._verify(path, broken_info)
        except (RuntimeError, UpdateSignatureError):
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

    try:
        with patch("elite.update_trust.PUBLIC_KEY_B64", public_key_b64), \
                patch("elite.updater.requests.get", side_effect=asset_responses(
                    good_manifest, good_signature, evil_manifest_redirect=True
                )):
            updater._verify(path, release_info())
    except (RuntimeError, UpdateSignatureError):
        pass
    else:
        raise AssertionError("cross-host manifest redirect was accepted")

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

# Concurrent API requests must reserve the updater before the network check;
# otherwise both can create workers targeting the same .new.exe path.
race_updater = Updater()
check_entered = threading.Event()
release_check = threading.Event()
check_calls = 0
check_calls_lock = threading.Lock()
race_results = []


def blocked_check(**_kwargs):
    global check_calls
    with check_calls_lock:
        check_calls += 1
    check_entered.set()
    assert release_check.wait(2), "concurrency test timed out"
    return {"available": False, "error": None}


def attempt_update():
    race_results.append(race_updater.start_update())


with patch("elite.updater.is_supported", return_value=True), \
        patch.object(race_updater, "check", side_effect=blocked_check):
    first = threading.Thread(target=attempt_update)
    first.start()
    assert check_entered.wait(2), "the first updater request never reached check()"
    second = threading.Thread(target=attempt_update)
    second.start()
    second.join(2)
    assert not second.is_alive(), "the duplicate updater request did not return promptly"
    release_check.set()
    first.join(2)

assert check_calls == 1, f"started {check_calls} concurrent update checks"
assert any("already in progress" in (error or "") for _ok, error in race_results)

broken_check = Updater()
with patch("elite.updater.is_supported", return_value=True), \
        patch.object(broken_check, "check", side_effect=AttributeError("bad metadata")):
    ok, error = broken_check.start_update()
assert ok is False and "Could not check" in error
assert broken_check.phase == "error" and not broken_check.running()

# Update failures name a cause the user can act on. Collapsing every OSError
# into one sentence is what made the Wacatac.B!ml false positive undiagnosable.
class VirusBlocked(OSError):
    winerror = 225


detail = _os_error_detail(VirusBlocked("access denied"))
assert "antivirus" in detail and "225" in detail, detail
assert _os_error_detail(OSError(errno.ENOSPC, "No space left")) == "the disk is full"
assert "locked" in _os_error_detail(PermissionError(13, "Permission denied"))
assert "removed it" in _os_error_detail(FileNotFoundError(2, "No such file"))

print("updater OK: Ed25519 authenticity, redirect validation, retained rollback, "
      "leftover sweep, diagnosable failures, atomic downloads/starts")
