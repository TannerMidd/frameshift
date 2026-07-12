"""Dependency-free discovery of addresses reachable by other LAN devices.

An internet route probe alone is not enough for this job: VPNs, containers and
virtual-machine switches frequently own the default route.  On Windows (the
primary Elite Dangerous platform) we ask the built-in networking cmdlets for
adapter metadata so a live physical Wi-Fi/Ethernet adapter wins over a tunnel.
Other platforms retain a conservative socket-only fallback.

All pairing surfaces use this module.  That keeps copied links, QR codes and
the headless-server console from selecting different interfaces.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


_VIRTUAL_MARKERS = (
    "container", "docker", "hamachi", "hyper-v", "loopback", "openvpn",
    "tap-", "tailscale", "teredo", "tunnel", "virtual", "virtualbox",
    "vmware", "vpn", "vethernet", "wireguard", "wsl", "zerotier",
)
_PHYSICAL_MARKERS = ("ethernet", "wi-fi", "wifi", "wireless", "wlan")
_CACHE_SECONDS = 30.0
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class AddressCandidate:
    address: str
    interface: str = ""
    description: str = ""
    physical: bool | None = None
    virtual: bool | None = None
    gateway: bool = False
    metric: int | None = None


def _usable_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        return False
    # Link-local IPv6 requires an interface scope in a URL and is therefore not
    # a safe shareable address. IPv4 APIPA remains useful on an ad-hoc LAN.
    if address.version == 6 and address.is_link_local:
        return False
    return address.is_private or address.is_link_local


def _range_preference(value: str) -> int:
    """Small fallback preference; adapter metadata remains authoritative."""
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if address.version == 6:
        return 5
    if address in ipaddress.ip_network("192.168.0.0/16"):
        return 30
    if address in ipaddress.ip_network("172.16.0.0/12"):
        return 20
    if address in ipaddress.ip_network("10.0.0.0/8"):
        return 10
    return 0


def _candidate_score(candidate: AddressCandidate):
    label = f"{candidate.interface} {candidate.description}".casefold()
    looks_virtual = candidate.virtual is True or any(
        marker in label for marker in _VIRTUAL_MARKERS
    )
    looks_physical = candidate.physical is True or any(
        marker in label for marker in _PHYSICAL_MARKERS
    )
    score = 0
    if looks_physical and not looks_virtual:
        score += 1000
    if candidate.physical is False:
        score -= 250
    if looks_virtual:
        score -= 1000
    if candidate.gateway:
        score += 150
    score += _range_preference(candidate.address)
    # Metrics only break ties inside the same adapter quality band. A metric-1
    # VPN must not outrank the physical LAN merely because it hijacked routing.
    metric = candidate.metric if isinstance(candidate.metric, int) else 9999
    return (-score, metric, ":" in candidate.address, candidate.address)


def rank_candidates(candidates) -> list[str]:
    """Return unique, reachable addresses in pairing-link preference order."""
    best = {}
    for candidate in candidates:
        if not isinstance(candidate, AddressCandidate):
            candidate = AddressCandidate(str(candidate))
        value = candidate.address.split("%", 1)[0]
        if not _usable_address(value):
            continue
        normalized = AddressCandidate(
            value, candidate.interface, candidate.description,
            candidate.physical, candidate.virtual, candidate.gateway,
            candidate.metric,
        )
        current = best.get(value)
        # Socket discovery has no adapter metadata.  When it rediscovers an
        # address already classified by the Windows IP Helper API, do not let
        # that anonymous duplicate erase a known VPN/virtual classification.
        # Otherwise a default-route VPN can launder itself back into the
        # candidate list as an apparently neutral address.
        has_metadata = bool(
            normalized.interface or normalized.description
            or normalized.physical is not None or normalized.virtual is not None
        )
        current_has_metadata = bool(
            current and (
                current.interface or current.description
                or current.physical is not None or current.virtual is not None
            )
        )
        if (
            current is None
            or (has_metadata and not current_has_metadata)
            or (has_metadata == current_has_metadata
                and _candidate_score(normalized) < _candidate_score(current))
        ):
            best[value] = normalized
    return [item.address for item in sorted(best.values(), key=_candidate_score)]


def _windows_candidates() -> list[AddressCandidate]:
    """Read adapter metadata through IP Helper; no subprocess/admin needed."""
    if os.name != "nt":
        return []
    # Structures are deliberately local: importing this module must remain
    # harmless on macOS/Linux, including source and PyInstaller builds.
    try:
        import ctypes
        from ctypes import wintypes

        class SocketAddress(ctypes.Structure):
            _fields_ = [("pointer", ctypes.c_void_p), ("length", ctypes.c_int)]

        class UnicastAddress(ctypes.Structure):
            pass

        UnicastAddress._fields_ = [
            ("length", wintypes.ULONG), ("flags", wintypes.DWORD),
            ("next", ctypes.POINTER(UnicastAddress)),
            ("address", SocketAddress),
        ]

        class AdapterAddress(ctypes.Structure):
            pass

        AdapterAddress._fields_ = [
            ("length", wintypes.ULONG), ("if_index", wintypes.DWORD),
            ("next", ctypes.POINTER(AdapterAddress)),
            ("adapter_name", ctypes.c_char_p),
            ("first_unicast", ctypes.POINTER(UnicastAddress)),
            ("first_anycast", ctypes.c_void_p),
            ("first_multicast", ctypes.c_void_p),
            ("first_dns_server", ctypes.c_void_p),
            ("dns_suffix", wintypes.LPWSTR),
            ("description", wintypes.LPWSTR),
            ("friendly_name", wintypes.LPWSTR),
            ("physical_address", ctypes.c_ubyte * 8),
            ("physical_address_length", wintypes.ULONG),
            ("flags", wintypes.ULONG), ("mtu", wintypes.ULONG),
            ("if_type", wintypes.ULONG), ("oper_status", ctypes.c_int),
            ("ipv6_if_index", wintypes.ULONG),
            ("zone_indices", wintypes.ULONG * 16),
            ("first_prefix", ctypes.c_void_p),
            ("transmit_link_speed", ctypes.c_ulonglong),
            ("receive_link_speed", ctypes.c_ulonglong),
            ("first_wins_server", ctypes.c_void_p),
            ("first_gateway", ctypes.c_void_p),
            ("ipv4_metric", wintypes.ULONG),
        ]

        get_adapters = ctypes.windll.iphlpapi.GetAdaptersAddresses
        get_adapters.argtypes = [
            wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p,
            ctypes.POINTER(AdapterAddress), ctypes.POINTER(wintypes.ULONG),
        ]
        get_adapters.restype = wintypes.ULONG
        size = wintypes.ULONG(15 * 1024)
        buffer = ctypes.create_string_buffer(size.value)
        # Skip nonessential lists, but request gateway metadata.
        result = get_adapters(
            socket.AF_INET, 0x2 | 0x4 | 0x8 | 0x80, None,
            ctypes.cast(buffer, ctypes.POINTER(AdapterAddress)), ctypes.byref(size),
        )
        if result == 111:  # ERROR_BUFFER_OVERFLOW
            buffer = ctypes.create_string_buffer(size.value)
            result = get_adapters(
                socket.AF_INET, 0x2 | 0x4 | 0x8 | 0x80, None,
                ctypes.cast(buffer, ctypes.POINTER(AdapterAddress)), ctypes.byref(size),
            )
        if result != 0:
            return []
    except (AttributeError, OSError, TypeError, ValueError):
        return []

    candidates = []
    adapter = ctypes.cast(buffer, ctypes.POINTER(AdapterAddress))
    while adapter:
        item = adapter.contents
        # IF_OPER_STATUS_UP == 1. Down/disconnected adapters cannot serve a
        # tablet even if Windows still retains an address for them.
        if item.oper_status == 1:
            interface = item.friendly_name or ""
            description = item.description or ""
            label = f"{interface} {description}".casefold()
            virtual = item.if_type in (23, 131) or any(
                marker in label for marker in _VIRTUAL_MARKERS
            )
            physical = item.if_type in (6, 71, 243, 244) and not virtual
            unicast = item.first_unicast
            while unicast:
                raw = unicast.contents.address.pointer
                length = unicast.contents.address.length
                value = ""
                if raw and length >= 8:
                    packed = ctypes.string_at(raw + 4, 4)
                    value = socket.inet_ntop(socket.AF_INET, packed)
                if value:
                    candidates.append(AddressCandidate(
                        value, interface, description, physical, virtual,
                        bool(item.first_gateway), int(item.ipv4_metric),
                    ))
                unicast = unicast.contents.next
        adapter = item.next
    return candidates


def _socket_candidates() -> list[AddressCandidate]:
    candidates = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            candidates.append(AddressCandidate(info[4][0]))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # No application data is sent. This is only a fallback candidate;
            # unlike the old implementation it receives no default-route bonus.
            probe.connect(("8.8.8.8", 80))
            candidates.append(AddressCandidate(probe.getsockname()[0]))
    except OSError:
        pass
    return candidates


def lan_addresses(*, refresh=False) -> list[str]:
    global _cached_at, _cached_addresses
    now = time.monotonic()
    with _cache_lock:
        if not refresh and _cached_addresses and now - _cached_at < _CACHE_SECONDS:
            return list(_cached_addresses)
        # Keep socket-discovered alternatives even when Windows metadata works;
        # this lets unusual-but-valid adapters remain available after the best.
        addresses = rank_candidates(_windows_candidates() + _socket_candidates())
        _cached_addresses = tuple(addresses)
        _cached_at = now
        return addresses


def best_lan_address(default="127.0.0.1") -> str:
    addresses = lan_addresses()
    return addresses[0] if addresses else default


def pairing_urls(path: str, port: int, *, scheme="http", preferred_host="") -> list[str]:
    """Build consistently ordered URLs for copy, QR, API and console output."""
    # Adapter ranking remains authoritative.  request.host is useful as a
    # known-working alternate, but an admin who opened Settings over a VPN must
    # not make that VPN address the QR/copy default for ordinary LAN devices.
    hosts = list(lan_addresses())
    if preferred_host:
        raw_host = str(preferred_host).strip()
        host = ""
        # request.host parsing gives callers a bare IPv6 literal.  Feeding that
        # back through urlsplit without brackets misreads ``fd00::1`` as host
        # ``fd00``.  Accept an IP literal directly before handling IPv4:port.
        try:
            host = str(ipaddress.ip_address(raw_host.split("%", 1)[0]))
        except ValueError:
            try:
                host = urlsplit(f"//{raw_host}").hostname or ""
            except ValueError:
                host = ""
        if _usable_address(host):
            hosts.append(host)
    own = socket.gethostname().strip().lower()
    if own:
        hosts.append(own + ".local")

    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    suffix = "" if default_port else f":{port}"
    urls = []
    for host in dict.fromkeys(hosts):
        rendered = f"[{host}]" if ":" in host else host
        urls.append(f"{scheme}://{rendered}{suffix}{path}")
    return urls
