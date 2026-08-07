"""Verify offline-signed release artifacts with Frameshift's pinned client key."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from elite.update_trust import (  # noqa: E402
    MAX_MANIFEST_BYTES,
    MAX_SIGNATURE_BYTES,
    UpdateSignatureError,
    verify_release_manifest,
)


REQUIRED_ASSET_NAMES = {"Frameshift.exe", "EliteTrader.exe"}


def _read_bounded(path: Path, maximum: int, label: str) -> bytes:
    try:
        if not path.is_file():
            raise OSError("file is missing")
        size = path.stat().st_size
        if size < 1 or size > maximum:
            raise OSError(f"size {size} is outside the allowed range")
        return path.read_bytes()
    except OSError as exc:
        raise UpdateSignatureError(f"{label} could not be read safely: {exc}") from exc


def verify_release_files(
    *,
    version: str,
    manifest_path: Path,
    signature_path: Path,
    asset_paths: list[Path],
) -> None:
    """Verify the manifest and exact bytes of both published executable names."""
    assets = {path.name: path for path in asset_paths}
    if len(assets) != len(asset_paths) or set(assets) != REQUIRED_ASSET_NAMES:
        raise UpdateSignatureError(
            "verification requires exactly Frameshift.exe and EliteTrader.exe"
        )

    manifest = _read_bounded(manifest_path, MAX_MANIFEST_BYTES, "release manifest")
    signature = _read_bounded(signature_path, MAX_SIGNATURE_BYTES, "release signature")
    for name in sorted(REQUIRED_ASSET_NAMES):
        path = assets[name]
        entry = verify_release_manifest(
            manifest,
            signature,
            expected_version=version,
            asset_name=name,
        )
        try:
            size = path.stat().st_size
            if size != entry["size"]:
                raise UpdateSignatureError(
                    f"{name} size does not match its signed manifest entry"
                )
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    raise UpdateSignatureError(f"{name} is not a Windows executable")
                stream.seek(0)
                for block in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(block)
        except OSError as exc:
            raise UpdateSignatureError(f"{name} could not be read safely: {exc}") from exc
        if not hmac.compare_digest(digest.hexdigest(), entry["sha256"]):
            raise UpdateSignatureError(f"{name} digest does not match its signed manifest entry")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--asset", action="append", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify_release_files(
            version=args.version,
            manifest_path=args.manifest,
            signature_path=args.signature,
            asset_paths=args.asset,
        )
    except UpdateSignatureError as exc:
        raise SystemExit(f"release verification failed: {exc}") from exc
    print(f"Verified offline signature for both release executables (v{args.version})")


if __name__ == "__main__":
    main()
