"""Pinned, zero-cost authenticity checks for packaged updates.

GitHub transports the release, but an Ed25519 signature made by the offline
Frameshift release key is the authority.  The corresponding private key never
lives in this repository or in a packaged application.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


PUBLIC_KEY_B64 = "U4y6wLljKX84OmrGlxhafxUcrbz4qf5ZEoeXvf3rsz8="
MANIFEST_NAME = "Frameshift-update.json"
SIGNATURE_NAME = MANIFEST_NAME + ".sig"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UpdateSignatureError(ValueError):
    """A release manifest is missing, malformed, or not publisher-signed."""


def _decode_base64(value: bytes, *, label: str, expected_size: int) -> bytes:
    try:
        decoded = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UpdateSignatureError(f"{label} is malformed") from exc
    if len(decoded) != expected_size:
        raise UpdateSignatureError(f"{label} has the wrong length")
    return decoded


def verify_release_manifest(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    *,
    expected_version: str,
    asset_name: str,
) -> dict[str, Any]:
    """Verify and return the signed metadata for one release asset."""
    if not manifest_bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise UpdateSignatureError("release manifest is missing or too large")
    if not signature_bytes or len(signature_bytes) > MAX_SIGNATURE_BYTES:
        raise UpdateSignatureError("release signature is missing or too large")

    public_key = _decode_base64(
        PUBLIC_KEY_B64.encode("ascii"), label="embedded update key", expected_size=32
    )
    signature = _decode_base64(signature_bytes, label="release signature", expected_size=64)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, manifest_bytes)
    except InvalidSignature as exc:
        raise UpdateSignatureError("release signature is invalid") from exc

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateSignatureError("signed release manifest is not valid JSON") from exc
    schema = manifest.get("schema") if isinstance(manifest, dict) else None
    if type(schema) is not int or schema != 1:
        raise UpdateSignatureError("signed release manifest has an unsupported schema")

    version = manifest.get("version")
    expected = str(expected_version or "").lstrip("vV")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version) or version != expected:
        raise UpdateSignatureError("signed release version does not match the GitHub release")
    assets = manifest.get("assets")
    entry = assets.get(asset_name) if isinstance(assets, dict) else None
    if not isinstance(entry, dict):
        raise UpdateSignatureError("signed release manifest does not contain this executable")
    digest = entry.get("sha256")
    size = entry.get("size")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise UpdateSignatureError("signed executable digest is malformed")
    if not isinstance(size, int) or isinstance(size, bool) or not 2 <= size <= MAX_ASSET_BYTES:
        raise UpdateSignatureError("signed executable size is invalid")
    return {"sha256": digest, "size": size, "version": version}
