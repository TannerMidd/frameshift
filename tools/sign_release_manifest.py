"""Create the deterministic Ed25519-signed manifest published with a release."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from elite.update_trust import (  # noqa: E402
    MAX_ASSET_BYTES,
    MAX_MANIFEST_BYTES,
    PUBLIC_KEY_B64,
)


REQUIRED_ASSET_NAMES = {"Frameshift.exe", "EliteTrader.exe"}


def _private_key(path: Path) -> Ed25519PrivateKey:
    try:
        if not path.is_file() or path.stat().st_size > 1024:
            raise OSError("private-key file is missing or unexpectedly large")
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise OSError("private-key file permissions must be owner-only (0600)")
        encoded = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise SystemExit(f"could not read the offline signing key safely: {exc}") from exc
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SystemExit("offline signing key is not valid base64") from exc
    if len(raw) != 32:
        raise SystemExit("offline signing key must encode exactly 32 bytes")
    key = Ed25519PrivateKey.from_private_bytes(raw)
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if base64.b64encode(public).decode("ascii") != PUBLIC_KEY_B64:
        raise SystemExit("release signing secret does not match the public key embedded in Frameshift")
    return key


def _asset(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise SystemExit(f"release asset is not a Windows executable: {path}")
        stream.seek(0)
        for block in iter(lambda: stream.read(1 << 20), b""):
            size += len(block)
            if size > MAX_ASSET_BYTES:
                raise SystemExit(f"release asset exceeds the updater safety limit: {path}")
            digest.update(block)
    if size < 2:
        raise SystemExit(f"release asset is empty: {path}")
    return {"sha256": digest.hexdigest(), "size": size}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--asset", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--private-key-file",
        type=Path,
        required=True,
        help="owner-only file containing the offline base64 Ed25519 private key",
    )
    args = parser.parse_args()
    version = args.version.lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("version must use MAJOR.MINOR.PATCH")

    assets: dict[str, dict[str, int | str]] = {}
    for path in args.asset:
        if not path.is_file() or path.name in assets:
            raise SystemExit(f"release asset is missing or duplicated: {path}")
        assets[path.name] = _asset(path)
    if set(assets) != REQUIRED_ASSET_NAMES:
        missing = ", ".join(sorted(REQUIRED_ASSET_NAMES - set(assets))) or "none"
        unexpected = ", ".join(sorted(set(assets) - REQUIRED_ASSET_NAMES)) or "none"
        raise SystemExit(
            "release inputs must be exactly Frameshift.exe and EliteTrader.exe; "
            f"missing: {missing}; unexpected: {unexpected}"
        )
    manifest = {"schema": 1, "version": version, "assets": assets}
    payload = (
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise SystemExit("release manifest exceeds the updater safety limit")
    signature = _private_key(args.private_key_file.resolve()).sign(payload)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    signature_path = args.output.with_name(args.output.name + ".sig")
    signature_path.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    print(f"Signed {len(assets)} release assets for v{version}")


if __name__ == "__main__":
    main()
