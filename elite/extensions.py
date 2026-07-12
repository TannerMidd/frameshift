"""Permissioned, local-first extension host for Frameshift.

Extensions live below ``data/extensions/<id>/manifest.json``.  The default
extension format is deliberately declarative: it can inspect journal events
and emit alerts or objective suggestions, but it cannot execute code.  This
keeps an extension pack portable, reviewable and safe to enable by simply
copying a directory.

An optional process adapter is available for advanced integrations.  It is
disabled until the user approves its exact pack-content fingerprint in
Frameshift-owned state outside the extension directory.  The child receives a
minimal JSON document on stdin and cannot call Frameshift APIs directly.  This
is a capability boundary, not an operating-system sandbox.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import marketdb

API_VERSION = 1
EXTENSIONS_DIR = marketdb.DATA_DIR / "extensions"
APPROVALS_PATH = marketdb.DATA_DIR / "extension-approvals.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_ALLOWED_PERMISSIONS = {
    "read:journal",
    "read:state",
    "emit:alert",
    "emit:objective",
}
_ALLOWED_ACTIONS = {"alert", "objective"}
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_ACTIONS_PER_EVENT = 16
_MAX_RULES = 256
_MAX_CONDITIONS = 64
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_MAX_PACK_FILES = 4096
_MAX_PACK_BYTES = 512 * 1024 * 1024
_FIELD_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
_CONDITION_OPERATORS = {"exists", "eq", "in", "min", "max"}


@dataclass(frozen=True)
class Extension:
    extension_id: str
    name: str
    version: str
    path: Path
    permissions: frozenset[str]
    rules: tuple[dict[str, Any], ...] = ()
    command: tuple[str, ...] = ()
    approved: bool = False
    fingerprint: str = ""


@dataclass
class ExtensionStatus:
    loaded: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


class ExtensionError(ValueError):
    pass


def _safe_read_manifest(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ExtensionError(f"cannot read manifest: {exc}") from exc
    if size > _MAX_MANIFEST_BYTES:
        raise ExtensionError("manifest is too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExtensionError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ExtensionError("manifest must be a JSON object")
    return value


def _approval_fingerprint(directory: Path, raw: dict[str, Any], command: tuple[str, ...]) -> str:
    """Bind approval to every executable input shipped by the pack.

    Hashing only ``command[0]`` is insufficient for interpreter adapters,
    helper executables, DLLs, plugins and data-driven code.  Treat the complete
    bounded pack as the reviewed unit.  The legacy self-approval marker is the
    sole exclusion because it is deliberately inert.
    """
    digest = hashlib.sha256()
    digest.update(json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    digest.update(b"\0frameshift-extension-v1\0")
    if command:
        executable = (directory / command[0]).resolve()
        try:
            size = executable.stat().st_size
            if not executable.is_file() or size > _MAX_EXECUTABLE_BYTES:
                raise ExtensionError("command executable is missing or too large")
            with open(executable, "rb") as stream:
                for block in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(block)
        except OSError as exc:
            raise ExtensionError("cannot read command executable") from exc
    try:
        root = directory.resolve(strict=True)
        files = []
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ExtensionError("extension packs cannot contain symbolic links")
            if not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            if relative.casefold() == "approved":
                continue
            files.append((relative, path))
    except ExtensionError:
        raise
    except (OSError, ValueError) as exc:
        raise ExtensionError("cannot enumerate extension pack") from exc
    if len(files) > _MAX_PACK_FILES:
        raise ExtensionError("extension pack contains too many files")

    total = 0
    for relative, path in sorted(files, key=lambda item: (item[0].casefold(), item[0])):
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            size = resolved.stat().st_size
            if size < 0 or size > _MAX_PACK_BYTES or total + size > _MAX_PACK_BYTES:
                raise ExtensionError("extension pack is too large")
            digest.update(b"file\0")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(size).encode("ascii"))
            digest.update(b"\0")
            read = 0
            with open(resolved, "rb") as stream:
                for block in iter(lambda: stream.read(1 << 20), b""):
                    read += len(block)
                    digest.update(block)
            if read != size:
                raise ExtensionError("extension pack changed while it was being reviewed")
            total += size
        except ExtensionError:
            raise
        except (OSError, ValueError) as exc:
            raise ExtensionError("cannot read extension pack") from exc
    return digest.hexdigest()


def _normalise_condition(index: int, path: Any, expected: Any) -> None:
    if not isinstance(path, str) or len(path) > 200 or not _FIELD_RE.fullmatch(path):
        raise ExtensionError(f"rule {index} has an invalid condition field")
    if not isinstance(expected, dict):
        return
    operators = set(expected)
    if not operators or operators - _CONDITION_OPERATORS:
        raise ExtensionError(f"rule {index} has unsupported condition operators")
    if "exists" in expected and not isinstance(expected["exists"], bool):
        raise ExtensionError(f"rule {index} condition exists must be true or false")
    if "in" in expected and (
        not isinstance(expected["in"], list) or len(expected["in"]) > 256
    ):
        raise ExtensionError(f"rule {index} condition in must be a bounded list")
    for operator in ("min", "max"):
        value = expected.get(operator)
        if operator in expected and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            raise ExtensionError(f"rule {index} condition {operator} must be a number")


def _normalise_extension(
    directory: Path, raw: dict[str, Any], approvals: dict[str, str] | None = None
) -> Extension:
    extension_id = str(raw.get("id") or "").strip().lower()
    if not _ID_RE.fullmatch(extension_id):
        raise ExtensionError("id must be 2-64 lowercase letters, digits, dots, dashes or underscores")
    if directory.name.lower() != extension_id:
        raise ExtensionError("directory name must match manifest id")
    try:
        api_version = int(raw.get("api_version", 0))
    except (TypeError, ValueError) as exc:
        raise ExtensionError("api_version must be an integer") from exc
    if api_version != API_VERSION:
        raise ExtensionError(f"unsupported api_version {api_version}; expected {API_VERSION}")

    permission_values = raw.get("permissions") or []
    if not isinstance(permission_values, list) or len(permission_values) > len(_ALLOWED_PERMISSIONS):
        raise ExtensionError("permissions must be a bounded list")
    if not all(isinstance(value, str) for value in permission_values):
        raise ExtensionError("permissions must contain strings")
    permissions = frozenset(permission_values)
    unknown = permissions - _ALLOWED_PERMISSIONS
    if unknown:
        raise ExtensionError("unknown permissions: " + ", ".join(sorted(unknown)))

    rules_value = raw.get("rules") or []
    if not isinstance(rules_value, list) or len(rules_value) > _MAX_RULES:
        raise ExtensionError(f"rules must be a list of at most {_MAX_RULES} items")
    rules: list[dict[str, Any]] = []
    for index, rule in enumerate(rules_value):
        if not isinstance(rule, dict):
            raise ExtensionError(f"rule {index + 1} must be an object")
        event = rule.get("event")
        action = rule.get("action")
        if not isinstance(event, str) or not event or len(event) > 100:
            raise ExtensionError(f"rule {index + 1} needs an event")
        if not isinstance(action, dict) or action.get("type") not in _ALLOWED_ACTIONS:
            raise ExtensionError(f"rule {index + 1} has an unsupported action")
        conditions = rule.get("when") or {}
        if not isinstance(conditions, dict) or len(conditions) > _MAX_CONDITIONS:
            raise ExtensionError(f"rule {index + 1} conditions must be a bounded object")
        for path, expected in conditions.items():
            _normalise_condition(index + 1, path, expected)
        required_field = "text" if action["type"] == "alert" else "title"
        if not isinstance(action.get(required_field), str) or not action[required_field]:
            raise ExtensionError(f"rule {index + 1} action needs {required_field}")
        if any(
            not isinstance(value, (str, int, float, bool)) and value is not None
            for key, value in action.items() if key != "type"
        ):
            raise ExtensionError(f"rule {index + 1} action values must be scalar")
        rules.append(rule)

    command_value = raw.get("command") or []
    if isinstance(command_value, str):
        command_value = [command_value]
    if (
        not isinstance(command_value, list)
        or len(command_value) > 32
        or not all(isinstance(v, str) and v and len(v) <= 4096 for v in command_value)
    ):
        raise ExtensionError("command must be a list of non-empty strings")
    command = tuple(command_value)
    if command:
        executable = (directory / command[0]).resolve()
        try:
            executable.relative_to(directory.resolve())
        except ValueError as exc:
            raise ExtensionError("command executable must stay inside the extension directory") from exc
    fingerprint = _approval_fingerprint(directory, raw, command)

    return Extension(
        extension_id=extension_id,
        name=str(raw.get("name") or extension_id).strip()[:100],
        version=str(raw.get("version") or "0").strip()[:40],
        path=directory,
        permissions=permissions,
        rules=tuple(rules),
        command=command,
        approved=bool(command and (approvals or {}).get(extension_id) == fingerprint),
        fingerprint=fingerprint,
    )


def _field(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for key in dotted.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _matches(rule: dict[str, Any], event: dict[str, Any]) -> bool:
    wanted = rule.get("event")
    if wanted != "*" and event.get("event") != wanted:
        return False
    conditions = rule.get("when") or {}
    if not isinstance(conditions, dict):
        return False
    for path, expected in conditions.items():
        actual = _field(event, str(path))
        if isinstance(expected, dict):
            if "exists" in expected and bool(actual is not None) != bool(expected["exists"]):
                return False
            if "eq" in expected and actual != expected["eq"]:
                return False
            if "in" in expected and actual not in expected["in"]:
                return False
            if "min" in expected and (not isinstance(actual, (int, float)) or actual < expected["min"]):
                return False
            if "max" in expected and (not isinstance(actual, (int, float)) or actual > expected["max"]):
                return False
        elif actual != expected:
            return False
    return True


def _render(value: Any, event: dict[str, Any]) -> Any:
    """Render ``{EventField}`` placeholders without evaluating expressions."""
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        found = _field(event, match.group(1))
        return "" if found is None else str(found)

    return re.sub(r"\{([A-Za-z0-9_.-]+)\}", replace, value)[:1000]


def _action_for(extension: Extension, action: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    action_type = action.get("type")
    required = "emit:alert" if action_type == "alert" else "emit:objective"
    if required not in extension.permissions:
        return None
    clean = {
        key: _render(value, event)
        for key, value in action.items()
        if key in {"type", "level", "code", "title", "text", "say", "category", "system", "station"}
    }
    clean["type"] = action_type
    clean["extension_id"] = extension.extension_id
    if action_type == "alert" and not clean.get("text"):
        return None
    if action_type == "objective" and not clean.get("title"):
        return None
    return clean


def _read_approvals(path: Path) -> dict[str, str]:
    try:
        if path.stat().st_size > 256 * 1024:
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    values = raw.get("approvals") if isinstance(raw, dict) else None
    if not isinstance(values, dict):
        return {}
    return {
        key: value.lower()
        for key, value in values.items()
        if isinstance(key, str) and _ID_RE.fullmatch(key)
        and isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value)
    }


def _write_approvals(path: Path, approvals: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    payload = {"version": 1, "approvals": dict(sorted(approvals.items()))}
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ExtensionError("extension approval could not be saved") from exc


def _process_approval_valid(extension: Extension, approvals_path: Path) -> bool:
    """Re-hash immediately before execution so edits cannot reuse approval."""
    if not extension.command or not extension.approved:
        return False
    try:
        raw = _safe_read_manifest(extension.path / "manifest.json")
        current = _approval_fingerprint(extension.path, raw, extension.command)
        approved = _read_approvals(approvals_path).get(extension.extension_id, "")
        return secrets.compare_digest(current, extension.fingerprint) and secrets.compare_digest(
            current, approved
        )
    except ExtensionError:
        return False


class ExtensionManager:
    def __init__(self, root: Path | None = None, approvals_path: Path | None = None):
        self.root = Path(root or EXTENSIONS_DIR)
        self.approvals_path = Path(
            approvals_path or (
                APPROVALS_PATH if self.root == EXTENSIONS_DIR
                else self.root.parent / "extension-approvals.json"
            )
        )
        self._lock = threading.Lock()
        self._approval_lock = threading.Lock()
        self._extensions: tuple[Extension, ...] = ()
        self._status = ExtensionStatus()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="extension")
        self._closed = False

    def reload(self) -> dict[str, Any]:
        loaded: list[Extension] = []
        errors: list[dict[str, str]] = []
        approvals = _read_approvals(self.approvals_path)
        try:
            directories = sorted(p for p in self.root.iterdir() if p.is_dir())
        except OSError:
            directories = []
        for directory in directories:
            manifest = directory / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                loaded.append(_normalise_extension(
                    directory, _safe_read_manifest(manifest), approvals
                ))
            except ExtensionError as exc:
                errors.append({"id": directory.name, "error": str(exc)})
        status = ExtensionStatus(
            loaded=[{
                "id": ext.extension_id,
                "name": ext.name,
                "version": ext.version,
                "permissions": sorted(ext.permissions),
                "mode": "process" if ext.command else "declarative",
                "approved": ext.approved if ext.command else True,
                "approval_required": bool(ext.command and not ext.approved),
                "fingerprint": ext.fingerprint[:16] if ext.command else None,
            } for ext in loaded],
            errors=errors,
        )
        with self._lock:
            self._extensions = tuple(loaded)
            self._status = status
        return self.snapshot()

    def approve_process(self, extension_id: str) -> dict[str, Any]:
        """Approve the currently loaded process adapter pack fingerprint.

        Returns the refreshed manager snapshot. Any changed file gets a new
        fingerprint and automatically returns the adapter to pending.
        """
        wanted = str(extension_id or "").strip().lower()
        if not _ID_RE.fullmatch(wanted):
            raise ExtensionError("invalid extension id")
        with self._approval_lock:
            self.reload()
            with self._lock:
                extension = next(
                    (item for item in self._extensions if item.extension_id == wanted), None
                )
            if extension is None:
                raise ExtensionError("extension is not loaded")
            if not extension.command:
                raise ExtensionError("declarative extensions do not require approval")
            approvals = _read_approvals(self.approvals_path)
            approvals[wanted] = extension.fingerprint
            _write_approvals(self.approvals_path, approvals)
            return self.reload()

    def revoke_process(self, extension_id: str) -> dict[str, Any]:
        """Revoke a process adapter approval and return the refreshed snapshot."""
        wanted = str(extension_id or "").strip().lower()
        if not _ID_RE.fullmatch(wanted):
            raise ExtensionError("invalid extension id")
        with self._approval_lock:
            approvals = _read_approvals(self.approvals_path)
            approvals.pop(wanted, None)
            _write_approvals(self.approvals_path, approvals)
            return self.reload()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "api_version": API_VERSION,
                "directory": str(self.root),
                "loaded": list(self._status.loaded),
                "errors": list(self._status.errors),
            }

    def subscribe(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def publish(self, event: dict[str, Any], state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not isinstance(event, dict):
            return []
        with self._lock:
            extensions = self._extensions
            listeners = tuple(self._listeners)
        actions: list[dict[str, Any]] = []
        for extension in extensions:
            if "read:journal" not in extension.permissions:
                continue
            for rule in extension.rules:
                try:
                    if _matches(rule, event):
                        action = _action_for(extension, rule["action"], event)
                        if action:
                            actions.append(action)
                            if len(actions) >= _MAX_ACTIONS_PER_EVENT:
                                break
                except (KeyError, TypeError, ValueError):
                    # A malformed condition must never prevent later rules or
                    # other extension packs from observing the event.
                    continue
            if extension.command and extension.approved:
                payload = {"api_version": API_VERSION, "event": event}
                if "read:state" in extension.permissions:
                    payload["state"] = state or {}
                with self._lock:
                    executor = None if self._closed else self._executor
                if executor is not None:
                    try:
                        executor.submit(self._run_process, extension, payload)
                    except RuntimeError:
                        # shutdown() may win the race after the lock is released.
                        pass
            if len(actions) >= _MAX_ACTIONS_PER_EVENT:
                break
        for action in actions:
            for listener in listeners:
                try:
                    listener(dict(action))
                except Exception:
                    continue
        return actions

    def shutdown(self, wait=True) -> None:
        """Stop accepting process-adapter work and join its worker threads."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
        executor.shutdown(wait=wait, cancel_futures=True)

    def _run_process(self, extension: Extension, payload: dict[str, Any]) -> None:
        if not _process_approval_valid(extension, self.approvals_path):
            return
        command = [str((extension.path / extension.command[0]).resolve()), *extension.command[1:]]
        try:
            result = subprocess.run(
                command,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                cwd=extension.path,
                timeout=3,
                check=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return
            values = json.loads(result.stdout)
            if isinstance(values, dict):
                values = [values]
            if not isinstance(values, list):
                return
            for raw in values[:_MAX_ACTIONS_PER_EVENT]:
                if not isinstance(raw, dict):
                    continue
                action = _action_for(extension, raw, payload["event"])
                if not action:
                    continue
                with self._lock:
                    listeners = tuple(self._listeners)
                for listener in listeners:
                    try:
                        listener(dict(action))
                    except Exception:
                        continue
        except (OSError, ValueError, subprocess.SubprocessError):
            return


EXTENSIONS = ExtensionManager()
