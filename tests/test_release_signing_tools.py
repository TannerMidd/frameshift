"""Offline release tools interoperate without exposing the real signing key."""

import base64
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from elite import update_trust  # noqa: E402
from tools import generate_update_signing_key as keygen  # noqa: E402
from tools import sign_release_manifest as signer  # noqa: E402
from tools import verify_release_manifest as verifier  # noqa: E402


def expect_exit(action):
    try:
        action()
    except SystemExit:
        return
    raise AssertionError("command unexpectedly succeeded")


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    key_path = root / "offline-release-key.txt"

    # The generator needs an explicit private-storage acknowledgement and
    # reserves the path rather than replacing an existing authority key.
    with patch.object(sys, "argv", ["generate_update_signing_key.py", str(key_path)]):
        expect_exit(keygen.main)
    assert not key_path.exists()
    with patch.object(sys, "argv", [
        "generate_update_signing_key.py",
        str(key_path),
        "--confirm-private-storage",
    ]):
        keygen.main()
    original_key_file = key_path.read_bytes()
    with patch.object(sys, "argv", [
        "generate_update_signing_key.py",
        str(key_path),
        "--confirm-private-storage",
    ]):
        expect_exit(keygen.main)
    assert key_path.read_bytes() == original_key_file
    if os.name == "nt":
        acl = subprocess.run(
            ["icacls", str(key_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
        assert "(I)" not in acl
        assert keygen._windows_principal().casefold() in acl.casefold()
    else:
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    private_raw = base64.b64decode(original_key_file.strip(), validate=True)
    private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    test_public_b64 = base64.b64encode(public_raw).decode("ascii")

    frameshift = root / "Frameshift.exe"
    legacy = root / "EliteTrader.exe"
    frameshift.write_bytes(b"MZ" + b"frameshift signed candidate")
    legacy.write_bytes(b"MZ" + b"legacy-name signed candidate")
    manifest = root / update_trust.MANIFEST_NAME
    signature = root / update_trust.SIGNATURE_NAME

    # Exercise the real signer with only an ephemeral key whose public half is
    # injected into this process. Client code retains its immutable production
    # default and exposes no runtime override.
    with patch.object(signer, "PUBLIC_KEY_B64", test_public_b64), patch.object(sys, "argv", [
        "sign_release_manifest.py",
        "--private-key-file", str(key_path),
        "--version", "9.8.7",
        "--asset", str(frameshift),
        "--asset", str(legacy),
        "--output", str(manifest),
    ]):
        signer.main()
    assert manifest.is_file() and signature.is_file()

    verifier_args = [
        "verify_release_manifest.py",
        "--version", "9.8.7",
        "--manifest", str(manifest),
        "--signature", str(signature),
        "--asset", str(frameshift),
        "--asset", str(legacy),
    ]
    with patch.object(update_trust, "PUBLIC_KEY_B64", test_public_b64), \
            patch.object(sys, "argv", verifier_args):
        verifier.main()

    # The production verifier rejects a post-signing mutation.
    frameshift.write_bytes(frameshift.read_bytes() + b"tampered")
    with patch.object(update_trust, "PUBLIC_KEY_B64", test_public_b64), \
            patch.object(sys, "argv", verifier_args):
        expect_exit(verifier.main)

    # bool is an int subclass in Python, but it is not a valid schema version.
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["schema"] = True
    boolean_schema = (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    boolean_signature = base64.b64encode(private_key.sign(boolean_schema)) + b"\n"
    with patch.object(update_trust, "PUBLIC_KEY_B64", test_public_b64):
        try:
            update_trust.verify_release_manifest(
                boolean_schema,
                boolean_signature,
                expected_version="9.8.7",
                asset_name="Frameshift.exe",
            )
        except update_trust.UpdateSignatureError:
            pass
        else:
            raise AssertionError("boolean schema version was accepted")

# CI builds only unsigned drafts; the separate manual workflow verifies public
# artifacts and never receives a private key.
candidate_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
    encoding="utf-8"
)
publish_workflow = (ROOT / ".github" / "workflows" / "publish-release.yml").read_text(
    encoding="utf-8"
)
assert "draft: true" in candidate_workflow
assert "FRAMESHIFT_UPDATE_SIGNING_KEY" not in candidate_workflow
assert "workflow_dispatch:" in publish_workflow
assert "tools/verify_release_manifest.py" in publish_workflow
assert "FRAMESHIFT_UPDATE_SIGNING_KEY" not in publish_workflow

print("release signing tools OK: offline key, real signer/verifier, draft-only CI")
