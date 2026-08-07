"""Generate Frameshift's offline Ed25519 release-signing key.

The private key is written only to an explicitly named, ignored local file.
Only the public key is printed, so command logs never disclose signing material.
"""

from __future__ import annotations

import argparse
import base64
import os
import stat
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _windows_principal() -> str:
    try:
        result = subprocess.run(
            ["whoami"], check=True, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not determine the Windows account for the key ACL") from exc
    principal = result.stdout.strip()
    if not principal:
        raise RuntimeError("could not determine the Windows account for the key ACL")
    return principal


def _restrict_windows_acl(path: Path) -> None:
    """Remove inherited access before any private-key bytes are written."""
    principal = _windows_principal()
    try:
        subprocess.run(
            ["icacls", str(path), "/grant:r", f"{principal}:(F)"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        subprocess.run(
            ["icacls", str(path), "/inheritance:r"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not restrict the Windows ACL on the signing key") from exc


def _write_private_key(path: Path, payload: bytes) -> None:
    """Create ``path`` once and fail rather than leave a broadly readable key."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        if os.name == "nt":
            # Harden the empty placeholder first, then write the secret only
            # after inherited ACL entries have been removed successfully.
            os.close(descriptor)
            descriptor = None
            _restrict_windows_acl(path)
            with path.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            os.chmod(path, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise RuntimeError("signing key permissions are not owner-only")
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="ignored file that will receive the private key")
    parser.add_argument(
        "--confirm-private-storage",
        action="store_true",
        help="confirm that output is private, offline storage and will never be committed",
    )
    args = parser.parse_args()

    if not args.confirm_private_storage:
        raise SystemExit(
            "refusing to create a release key without --confirm-private-storage"
        )

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    private_payload = base64.b64encode(private_raw) + b"\n"
    try:
        _write_private_key(output, private_payload)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace existing signing key: {output}") from exc
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f"could not create a permission-safe signing key: {exc}") from exc

    print(f"Private key written to: {output}")
    print("Public key: " + base64.b64encode(public_raw).decode("ascii"))


if __name__ == "__main__":
    main()
