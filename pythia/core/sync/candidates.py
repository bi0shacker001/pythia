"""Pure candidate-address logic (2026-08-08, "relay-assisted rendezvous and
direct-path candidate orchestration"; extended 2026-08-09, "internal IPv6
direct connectivity and dual-stack route selection"). No I/O, no network --
merges, normalizes, deduplicates, and ranks direct-path connection
candidates gathered from LAN discovery, global discovery, pairing
invitations, and the trust store's own persisted `known_addresses`, so the
connection racer has one clean list to attempt instead of each source's raw
(and possibly overlapping, malformed, or adversarial) address strings.

Every function here is defensive against a forged/malformed candidate from
an untrusted source (a LAN broadcast packet, a global-discovery response, a
relay-relayed payload someone tried to tamper with before it was even
signature-checked) -- normalize_candidate() never raises, it just returns
None for anything that isn't a real, well-formed, connectable endpoint.

**IPv6 scoped literals** (RFC 4007 zone identifiers, RFC 4291 link-local
addressing): this module's own candidate string format is `[addr%zone]:port`
-- plain `%zone` inside the brackets, NOT the RFC 6874 URI form's `%25zone`
percent-encoding, since these strings are never embedded in an actual URI
anywhere in this codebase (a deliberate, documented format choice, not an
oversight). A zone identifier is host-local and non-portable (Windows uses
a numeric interface index, Linux/macOS an interface name; the two are never
interchangeable) -- a link-local address is REJECTED by
normalize_candidate() unless it carries one (an unscoped link-local literal
is genuinely ambiguous, not merely inconvenient), and a scoped candidate is
never allowed to escape this host: see strip_unpublishable_candidates()
below, used by every caller that hands candidates to another device
(pairing invitations, relay offers, global-discovery announcements)."""

from __future__ import annotations

import ipaddress
import re

_HOST_PORT_RE = re.compile(r"^(?P<host>\[[0-9a-fA-F:]+(?:%[0-9A-Za-z._-]{1,32})?\]|[^:\[\]]+):(?P<port>[0-9]{1,5})$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")
_LOCAL_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_LOCAL_IPV6_NETWORK = ipaddress.IPv6Network("fc00::/7")
_DOCUMENTATION_IPV6_NETWORK = ipaddress.IPv6Network("2001:db8::/32")


def _is_valid_hostname(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    labels = host.split(".")
    return all(_LABEL_RE.match(label) for label in labels)


def normalize_candidate(address: str) -> str | None:
    """Parses a real `host:port` endpoint -- IPv4 (`1.2.3.4:24030`), IPv6
    (`[::1]:24030`, or a scoped link-local literal `[fe80::1%eth0]:24030`),
    or hostname (`example.internal:24030`) -- into a canonical string, or
    returns None for anything malformed OR simply not connectable (never
    raises). A link-local IPv6 address without a real zone identifier is
    rejected outright -- it is genuinely ambiguous, not merely inconvenient
    (RFC 4007) -- and a zone attached to anything OTHER than a link-local
    address is rejected too (a real, well-formed IPv6 candidate never needs
    one). Unspecified, multicast, and RFC 3849 documentation-range IPv6
    addresses are rejected the same way IPv4's own reserved/documentation
    ranges already are."""
    if not isinstance(address, str):
        return None
    address = address.strip()
    if not address or "\n" in address or "\r" in address or "\x00" in address:
        return None
    match = _HOST_PORT_RE.match(address)
    if not match:
        return None
    host_part = match.group("host")
    try:
        port = int(match.group("port"))
    except ValueError:
        return None
    if not (0 < port < 65536):
        return None

    if host_part.startswith("[") and host_part.endswith("]"):
        return _normalize_ipv6_literal(host_part[1:-1], port)

    try:
        ip4 = ipaddress.IPv4Address(host_part)
        return f"{ip4}:{port}"
    except ValueError:
        pass

    if not _is_valid_hostname(host_part):
        return None
    return f"{host_part}:{port}"


def _normalize_ipv6_literal(inner: str, port: int) -> str | None:
    raw_address, sep, zone = inner.partition("%")
    try:
        # Construct from the FULL literal (including "%zone", if any) so that
        # ip6.compressed/.scope_id reflect the real zone -- constructing from
        # raw_address alone would silently discard it.
        ip6 = ipaddress.IPv6Address(inner)
    except ValueError:
        return None
    # Loopback (::1) is technically inside IANA's broader "reserved" IPv6
    # range per Python's own classification, but it is genuinely
    # connectable (to this same host) and legitimately useful -- excluded
    # from the reserved rejection below on purpose, not an oversight.
    if ip6.is_unspecified or ip6.is_multicast or (ip6.is_reserved and not ip6.is_loopback) or ip6 in _DOCUMENTATION_IPV6_NETWORK:
        return None
    if ip6.ipv4_mapped is not None:
        # An IPv4-mapped IPv6 literal (`::ffff:1.2.3.4`) is never a real,
        # distinct candidate -- reject it rather than let it slip past
        # IPv4-specific checks elsewhere under a v6-shaped wrapper. A real
        # IPv4 candidate must be expressed as plain IPv4.
        return None
    if ip6.is_link_local:
        if not sep or not zone:
            # A bare link-local literal is genuinely ambiguous -- it can
            # mean a different real host depending on which local interface
            # is used to reach it. Never accepted as a usable candidate.
            return None
        # ip6.compressed already includes the real "%zone" suffix -- do not
        # append it a second time.
        return f"[{ip6.compressed}]:{port}"
    if sep:
        # A zone identifier only ever makes sense for a link-local (or
        # link-local-scope multicast, already rejected above) address --
        # attaching one to anything else is malformed input, not a real
        # candidate.
        return None
    return f"[{ip6.compressed}]:{port}"


def _host_of(normalized: str) -> str:
    host = normalized.rsplit(":", 1)[0]
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def is_private_or_local(address: str) -> bool:
    """True for a candidate likely reachable directly on today's LAN
    (private/loopback/link-local IPv4 or IPv6) -- used to rank same-network
    candidates ahead of public/global ones, since a direct LAN connection is
    both faster and needs no discovery/relay help at all. A hostname (not a
    literal IP) is never considered local -- we have no way to know its
    resolved address's scope without a real DNS lookup, which this pure
    function deliberately never performs."""
    normalized = normalize_candidate(address)
    if normalized is None:
        return False
    try:
        ip = ipaddress.ip_address(_host_of(normalized))
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in network for network in _LOCAL_IPV4_NETWORKS)
    return ip in _LOCAL_IPV6_NETWORK


def merge_candidates(*sources: list[str]) -> list[str]:
    """Merges candidate lists from every real source (LAN, global
    discovery, pairing-supplied `known_addresses`, the trust store's own
    persisted `known_addresses`) -- normalizes and deduplicates, first
    occurrence wins (callers should pass their most-current/most-trusted
    source first). A malformed or forged entry from any source is silently
    dropped, never propagated to the racer."""
    seen: set[str] = set()
    merged: list[str] = []
    for source in sources:
        for raw in source or []:
            normalized = normalize_candidate(raw)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def rank_candidates(candidates: list[str]) -> list[str]:
    """Local/private candidates first (most likely directly reachable,
    lowest latency), then everything else -- a stable partition, each
    group keeping its original relative order, never a re-shuffle."""
    local = [c for c in candidates if is_private_or_local(c)]
    other = [c for c in candidates if not is_private_or_local(c)]
    return local + other


def merge_and_rank(*sources: list[str]) -> list[str]:
    """Convenience: merge_candidates() then rank_candidates() in one call --
    the shape SyncService's own candidate gathering actually wants."""
    return rank_candidates(merge_candidates(*sources))


def _family_of(candidate: str) -> str:
    try:
        ip = ipaddress.ip_address(_host_of(candidate).partition("%")[0])
    except ValueError:
        return "other"
    return "ipv6" if isinstance(ip, ipaddress.IPv6Address) else "ipv4"


def interleave_families(candidates: list[str]) -> list[str]:
    """Reorders a candidate list so IPv6 and non-IPv6 entries alternate,
    IPv6 first -- Happy Eyeballs-style (RFC 8305) family interleaving, so a
    bounded connection race (service.race_candidates()) tries both families
    early instead of exhausting one before ever attempting the other.
    Relative order within each family is preserved -- a stable interleave,
    never a re-rank (2026-08-09, "internal IPv6 direct connectivity and
    dual-stack route selection")."""
    ipv6 = [c for c in candidates if _family_of(c) == "ipv6"]
    rest = [c for c in candidates if _family_of(c) != "ipv6"]
    result: list[str] = []
    i = j = 0
    while i < len(ipv6) or j < len(rest):
        if i < len(ipv6):
            result.append(ipv6[i])
            i += 1
        if j < len(rest):
            result.append(rest[j])
            j += 1
    return result


def rank_and_interleave(*sources: list[str]) -> list[str]:
    """merge_and_rank() (local-first) then interleave_families() *within*
    each of the local/non-local partitions -- the real ordering
    connect_to_trusted()'s dual-stack race wants: still prefer a directly-
    reachable local candidate over a public one, but never exhaust IPv4
    (or IPv6) within a partition before trying the other family."""
    merged = merge_candidates(*sources)
    local = [c for c in merged if is_private_or_local(c)]
    other = [c for c in merged if not is_private_or_local(c)]
    return interleave_families(local) + interleave_families(other)


def has_zone(candidate: str) -> bool:
    """True if a real, already-normalized candidate carries an IPv6 zone
    identifier -- always a link-local address per normalize_candidate()'s
    own validation. A zone is host-local and non-portable (2026-08-09,
    "internal IPv6 direct connectivity and dual-stack route selection")."""
    return "%" in _host_of(candidate)


def strip_unpublishable_candidates(candidates: list[str]) -> list[str]:
    """Removes every scoped (zone-carrying) candidate -- these are
    real and useful for THIS host's own direct-connection racing, but a
    zone identifier is meaningless (and actively misleading) on any other
    device, so this is what every caller that hands candidates to another
    device must apply first: pairing invitations, relay candidate offers,
    and global-discovery announcements. Never mutates the input list."""
    return [c for c in candidates if not has_zone(c)]
