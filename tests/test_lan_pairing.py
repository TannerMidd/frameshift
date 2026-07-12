"""Pairing links prefer the reachable LAN over VPN/virtual adapters."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite import network
from elite.network import AddressCandidate, pairing_urls, rank_candidates
from elite.security import SecurityManager
from elite.server import create_app
from elite.state import AppState
from app import startup_pairing_url


# Regression for the reported machine: NordLynx owned the internet route and
# the old route probe published 10.5.0.2 instead of Ethernet's 192.168.1.65.
reported = rank_candidates([
    AddressCandidate(
        "10.5.0.2", "NordLynx", "NordLynx Tunnel #2",
        physical=False, virtual=True, gateway=True, metric=1,
    ),
    AddressCandidate(
        "192.168.1.65", "Ethernet 2", "Realtek PCIe GbE Controller",
        physical=True, virtual=False, gateway=True, metric=25,
    ),
    AddressCandidate(
        "172.24.32.1", "vEthernet (Default Switch)", "Hyper-V Virtual Ethernet Adapter",
        physical=False, virtual=True, metric=15,
    ),
])
assert reported[0] == "192.168.1.65", reported

# Do not hard-code 192.168 as universally correct. Adapter quality beats the
# RFC1918 range when a real home/office LAN uses 10/8.
ten_lan = rank_candidates([
    AddressCandidate(
        "192.168.56.1", "VirtualBox Host-Only Network", "Virtual Ethernet",
        physical=False, virtual=True, metric=1,
    ),
    AddressCandidate(
        "10.20.30.4", "Wi-Fi", "Intel Wireless Adapter",
        physical=True, virtual=False, gateway=True, metric=40,
    ),
])
assert ten_lan[0] == "10.20.30.4", ten_lan

# When adapter metadata is unavailable, common home-LAN ranges are a safer
# fallback than the VPN address returned by an internet route probe.
assert rank_candidates(["10.5.0.2", "172.24.32.1", "192.168.1.65"])[0] == "192.168.1.65"

original = network.lan_addresses
network.lan_addresses = lambda **_kwargs: [
    "192.168.1.65", "172.24.32.1", "10.5.0.2"
]
try:
    urls = pairing_urls("/?pair=secret", 8667)
    assert urls[0] == "http://192.168.1.65:8667/?pair=secret", urls
    assert startup_pairing_url("/?pair=secret", 8667) == urls[0]

    # A request host remains an alternate, not the default. Opening Settings
    # over a VPN must not replace the ranked physical LAN address in copy/QR.
    urls = pairing_urls(
        "/?pair=secret", 8667, preferred_host="10.5.0.2:8667"
    )
    assert urls[0] == "http://192.168.1.65:8667/?pair=secret", urls
    assert "http://10.5.0.2:8667/?pair=secret" in urls

    with tempfile.TemporaryDirectory() as td:
        import elite.qrcode as qrcode

        encoded = []
        original_svg = qrcode.svg
        qrcode.svg = lambda value: encoded.append(value) or "<?xml captured?>"
        app = create_app(
            AppState(), security_manager=SecurityManager(Path(td))
        )
        app.testing = True
        client = app.test_client()

        # Status drives Settings' copy button and QR. Rotation is the API used
        # by "NEW ONE-TIME LINK". Both must expose the same primary address.
        status = client.get(
            "/api/security/status", headers={"Host": "localhost:8667"}
        ).get_json()["pairing"]
        assert status["urls"][0].startswith("http://192.168.1.65:8667/")
        assert status["qr_svg"].startswith("<?xml")
        assert encoded[-1] == status["urls"][0]

        rotated = client.post(
            "/api/security/pairing-code",
            headers={"Host": "localhost:8667"},
            json={"scopes": ["admin"]},
        ).get_json()
        assert rotated["urls"][0].startswith("http://192.168.1.65:8667/")
        assert rotated["qr_svg"].startswith("<?xml")
        assert encoded[-1] == rotated["urls"][0]
        qrcode.svg = original_svg
finally:
    network.lan_addresses = original

# With no usable numeric interface, mDNS remains a zero-configuration fallback.
original_addresses = network.lan_addresses
original_hostname = network.socket.gethostname
network.lan_addresses = lambda **_kwargs: []
network.socket.gethostname = lambda: "gaming-pc"
try:
    assert pairing_urls("/?pair=secret", 8667) == [
        "http://gaming-pc.local:8667/?pair=secret"
    ]
finally:
    network.lan_addresses = original_addresses
    network.socket.gethostname = original_hostname

# If even a hostname is unavailable, startup reports an honest local fallback
# instead of inventing an unreachable LAN address.
network.lan_addresses = lambda **_kwargs: []
network.socket.gethostname = lambda: ""
try:
    assert pairing_urls("/?pair=secret", 8667) == []
    assert startup_pairing_url("/?pair=secret", 8667) == \
        "http://127.0.0.1:8667/?pair=secret"
finally:
    network.lan_addresses = original_addresses
    network.socket.gethostname = original_hostname

# A request parsed by Flask supplies a bare IPv6 literal. Preserve and bracket
# it rather than reparsing ``fd00::1`` as hostname ``fd00``.
network.lan_addresses = lambda **_kwargs: []
network.socket.gethostname = lambda: ""
try:
    assert pairing_urls("/?pair=secret", 8667, preferred_host="fd12:3456::1") == [
        "http://[fd12:3456::1]:8667/?pair=secret"
    ]
    assert pairing_urls("/?pair=secret", 8667, preferred_host="[fd12:3456::2]:8667") == [
        "http://[fd12:3456::2]:8667/?pair=secret"
    ]
finally:
    network.lan_addresses = original_addresses
    network.socket.gethostname = original_hostname

print("LAN pairing OK: physical adapters win consistently for copy, QR, API, and console")
