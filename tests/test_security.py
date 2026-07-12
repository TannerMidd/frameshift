"""LAN pairing, authorization, CSRF, headers, path confinement, and limits."""

import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite.security import RateLimiter, SecurityManager, is_loopback
from elite.server import _journal_path, create_app
from elite.state import AppState


with tempfile.TemporaryDirectory() as td:
    assert is_loopback("127.0.0.1") and is_loopback("::1")
    assert is_loopback("::ffff:127.0.0.1") and not is_loopback("192.168.1.2")
    data_dir = Path(td)
    clock = [1_000.0]
    manager = SecurityManager(data_dir, now=lambda: clock[0])

    # Capabilities expire and are strictly one-use. Device credentials persist
    # as digests only and permissions follow read < control < admin.
    grant = manager.issue_pairing(["control"], ttl_seconds=60)
    assert manager.pair("wrong", "Tablet", "192.168.1.9") is None
    paired = manager.pair(grant["code"], "  Bridge   iPad\n", "192.168.1.9")
    assert paired
    token, device = paired
    assert device["name"] == "Bridge iPad"
    assert device["scopes"] == ["read", "control"]
    assert manager.pair(grant["code"], "Replay") is None
    raw = (data_dir / "security.json").read_text(encoding="utf-8")
    assert token not in raw and "token_hash" in raw
    assert manager.authenticate(token, "192.168.1.10")["id"] == device["id"]
    expired = manager.rotate_pairing(["read"], ttl_seconds=60)
    clock[0] += 61
    assert manager.pair(expired["code"], "Late device") is None

    reloaded = SecurityManager(data_dir)
    assert reloaded.authenticate(token)["id"] == device["id"]
    updated = reloaded.update_device(device["id"], scopes=["read"])
    assert updated["scopes"] == ["read"]
    assert reloaded.revoke(device["id"])
    assert reloaded.authenticate(token) is None

    # A fresh manager backs the HTTP integration tests.
    manager = SecurityManager(data_dir)
    app = create_app(AppState(), security_manager=manager)
    app.testing = True
    local = app.test_client()
    remote = app.test_client()
    remote_env = {"REMOTE_ADDR": "192.168.1.50"}
    host = {"Host": "192.168.1.2:8666"}
    same_origin = {**host, "Origin": "http://192.168.1.2:8666"}

    # Localhost stays zero-friction; static bootstrap is public, commander
    # state is not. All responses receive browser hardening headers.
    response = local.get("/api/state")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    response = remote.get("/", headers=host, environ_base=remote_env)
    assert response.status_code == 200
    response = remote.get("/api/state", headers=host, environ_base=remote_env)
    assert response.status_code == 401 and response.get_json()["pairing_required"]

    # Polling status must preserve a deliberately least-privilege grant.  It
    # used to replace this link with a new all-scope capability, invalidating
    # the link on screen and silently defeating scoped enrollment.
    restricted = local.post(
        "/api/security/pairing-code", json={"scopes": ["read"], "ttl_seconds": 300}
    ).get_json()
    polled = local.get("/api/security/status").get_json()["pairing"]
    assert polled["path"] == restricted["path"]
    assert polled["scopes"] == ["read"]
    # Restore the normal dashboard/startup grant for the integration flow.
    local.post("/api/security/pairing-code", json={"scopes": ["admin"]})

    # The desktop publishes a link; the remote page exchanges it silently for
    # an HttpOnly cookie. No account, password, or pasted API key is involved.
    status = local.get("/api/security/status").get_json()
    code = parse_qs(urlsplit(status["pairing"]["path"]).query)["pair"][0]
    response = remote.post(
        "/api/security/pair", headers=host, environ_base=remote_env,
        json={"code": code, "device_name": "Cockpit tablet"},
    )
    assert response.status_code == 403  # remote side effects need origin proof
    response = remote.post(
        "/api/security/pair", headers=same_origin, environ_base=remote_env,
        json={"code": code, "device_name": "Cockpit tablet", "return_token": True},
    )
    assert response.status_code == 200, response.get_json()
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert "SameSite=Strict" in response.headers["Set-Cookie"]
    admin_token = response.get_json()["token"]
    assert remote.get("/api/state", headers=host, environ_base=remote_env).status_code == 200
    assert remote.get("/api/security/devices", headers=host,
                      environ_base=remote_env).status_code == 200

    # Cookie CSRF is blocked. A bearer client is not forced to invent browser
    # headers, but still has to possess the unguessable credential.
    response = remote.post("/api/plot/cancel", headers=host, environ_base=remote_env)
    assert response.status_code == 403
    response = remote.post(
        "/api/plot/cancel", headers={**host, "Authorization": f"Bearer {admin_token}"},
        environ_base=remote_env,
    )
    # The route was authorized (whether an optional platform driver is present
    # is outside this security test).
    assert response.status_code != 401 and response.status_code != 403

    # Create a read-only capability/device. Reads work; game control and device
    # administration do not. Legacy GET speech is never allowed over the LAN.
    read_grant = manager.rotate_pairing(["read"])
    reader_result = manager.pair(read_grant["code"], "Status display", "192.168.1.60")
    reader_token, reader_device = reader_result
    reader_headers = {**host, "Authorization": f"Bearer {reader_token}"}
    assert remote.get("/api/state", headers=reader_headers,
                      environ_base={"REMOTE_ADDR": "192.168.1.60"}).status_code == 200
    response = remote.post("/api/launch-game", headers=reader_headers,
                           environ_base={"REMOTE_ADDR": "192.168.1.60"})
    assert response.status_code == 403 and response.get_json()["required_scope"] == "control"
    response = remote.get("/api/security/devices", headers=reader_headers,
                          environ_base={"REMOTE_ADDR": "192.168.1.60"})
    assert response.status_code == 403 and response.get_json()["required_scope"] == "admin"
    assert remote.get("/api/settings", headers=reader_headers,
                      environ_base={"REMOTE_ADDR": "192.168.1.60"}).status_code == 403
    assert remote.get("/api/journal-dir/validate", headers=reader_headers,
                      environ_base={"REMOTE_ADDR": "192.168.1.60"}).status_code == 403

    # Process-extension approval is an admin operation. A local/admin request
    # reaches the manager, while read/control capabilities are rejected before
    # any approval state can change.
    from elite import extensions

    original_extensions = extensions.EXTENSIONS

    class FakeExtensions:
        def __init__(self):
            self.calls = []

        def approve_process(self, extension_id):
            self.calls.append(("approve", extension_id))
            return {"loaded": [{"id": extension_id, "approved": True}]}

        def revoke_process(self, extension_id):
            self.calls.append(("revoke", extension_id))
            return {"loaded": [{"id": extension_id, "approved": False}]}

    fake_extensions = FakeExtensions()
    extensions.EXTENSIONS = fake_extensions
    try:
        approved = local.post("/api/extensions/process.pack/approve")
        revoked = local.post("/api/extensions/process.pack/revoke")
        assert approved.status_code == 200 and revoked.status_code == 200
        assert fake_extensions.calls == [
            ("approve", "process.pack"), ("revoke", "process.pack"),
        ]
        denied = remote.post(
            "/api/extensions/process.pack/approve", headers=reader_headers,
            environ_base={"REMOTE_ADDR": "192.168.1.60"},
        )
        assert denied.status_code == 403 and denied.get_json()["required_scope"] == "admin"
        manager.update_device(reader_device["id"], scopes=["control"])
        denied_control = remote.post(
            "/api/extensions/process.pack/revoke", headers=reader_headers,
            environ_base={"REMOTE_ADDR": "192.168.1.60"},
        )
        assert denied_control.status_code == 403
        manager.update_device(reader_device["id"], scopes=["read"])
        assert len(fake_extensions.calls) == 2
    finally:
        extensions.EXTENSIONS = original_extensions
    response = remote.get("/api/speak?text=nope", headers={**reader_headers, **same_origin},
                          environ_base={"REMOTE_ADDR": "192.168.1.60"})
    assert response.status_code == 403  # read-only device lacks control first

    # Admin can change permissions and revoke devices; revocation is immediate.
    response = local.patch(f"/api/security/devices/{reader_device['id']}",
                           json={"scopes": ["control"]})
    assert response.status_code == 200
    response = remote.get("/api/speak?text=nope",
                          headers={**same_origin, "Authorization": f"Bearer {reader_token}"},
                          environ_base={"REMOTE_ADDR": "192.168.1.60"})
    assert response.status_code in (404, 405), response.status_code
    assert local.delete(f"/api/security/devices/{reader_device['id']}").status_code == 200
    assert remote.get("/api/state", headers=reader_headers,
                      environ_base={"REMOTE_ADDR": "192.168.1.60"}).status_code == 401

    # Cross-site browser signals are rejected even with valid credentials.
    response = remote.post(
        "/api/nope",
        headers={**host, "Origin": "https://evil.example", "Authorization": f"Bearer {admin_token}"},
        environ_base=remote_env,
    )
    assert response.status_code == 403

    # Stored journal paths are absolute, confined below a plausible player
    # root, and cannot name the root itself or an arbitrary OS directory.
    safe = Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
    normalized, reason = _journal_path(str(safe))
    assert normalized and reason is None
    assert _journal_path("relative/journals")[0] is None
    assert _journal_path(str(Path.home()))[0] is None
    outside = Path(td) / "not-the-player-profile"
    if not outside.is_relative_to(Path.home()):
        assert _journal_path(str(outside))[0] is None

    # The limiter returns a useful retry value and recovers when its window
    # passes (without sleeps or flaky wall-clock timing).
    monotonic = [10.0]
    limiter = RateLimiter(now=lambda: monotonic[0])
    assert limiter.check("pair", 2, 60)[0]
    assert limiter.check("pair", 2, 60)[0]
    allowed, retry = limiter.check("pair", 2, 60)
    assert not allowed and retry > 0
    monotonic[0] += 61
    assert limiter.check("pair", 2, 60)[0]

print("security OK: capability pairing, scoped devices, CSRF, headers, paths, limits")
