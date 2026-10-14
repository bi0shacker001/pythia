"""Core sync lifecycle orchestrator (2026-08-08, "optional core sync service
and native device/transfer workflow"). Ties together every previously-built,
independently-tested pythia.core.sync module (identity, trust, pairing,
config, protocol, direct TCP/TLS, LAN discovery) into one real, start/
stop-able service. No orchestration code existed anywhere before this --
confirmed by grepping the whole tree for "service"/"orchestrat"/"lifecycle"/
"start_sync" before writing a line here.

**Dormant by construction**: `start()` is a real no-op
(`{"status": "disabled"}`) unless `pythia.core.sync.config.is_enabled()` is
true -- no identity/cert files, no sockets, no background tasks, ever, while
disabled. Identity creation (a real side effect: writes key.pem/cert.pem)
only happens the first time `start()` actually runs with sync enabled --
never merely from importing this module or constructing a `SyncService`.

Cancellation/shutdown is real: `stop()` closes the LAN discovery transport
and the direct-connection listener and cancels their background tasks --
nothing is left running after it returns.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import ipaddress
import socket
import time
import uuid
from pathlib import Path

from pythia.core.sync import candidates as candidates_module
from pythia.core.sync import config as sync_config
from pythia.core.sync import lan, metadata_session, nat, protocol, relay, transfer_offer, trust
from pythia.core.sync.connectivity import direct_tcp

DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 24030  # fixed, unregistered range; real user setting, see get_listen_port() below

# Bounded retry/backoff for unreachable trusted peers (2026-08-08, Codex sync
# review, "Add bounded retry/backoff for unreachable trusted peers, cancel it
# on disable, and expose next-attempt/last-error honestly"). Exponential,
# capped, and finite -- never an infinite hot-retry loop.
RETRY_CHECK_INTERVAL = 5.0
RETRY_BASE_SECONDS = 5.0
RETRY_MAX_SECONDS = 300.0
RETRY_MAX_ATTEMPTS = 8

# Floor on the global-discovery reannounce interval, regardless of what the
# server's own Reannounce-After header says -- prevents a misbehaving or
# misconfigured server from causing a hot HTTPS loop. Overridable at module
# scope for tests (same pattern as RETRY_CHECK_INTERVAL above).
GLOBAL_DISCOVERY_MIN_INTERVAL = 60.0

# Internal NAT traversal (2026-08-08, "internal NAT traversal and direct-
# route publication"): try PCP first (fastest, most modern), then NAT-PMP,
# then UPnP IGD (slowest, most widely deployed) -- see nat.py's own
# module docstring for why in that order. Renew well before the granted
# lifetime expires; never wait until the last moment.
NAT_PROTOCOL_ORDER = ("pcp", "nat-pmp", "upnp")
NAT_REQUESTED_LIFETIME = 7200
NAT_RENEW_FRACTION = 0.5  # renew at half the granted lifetime
NAT_MIN_RENEW_INTERVAL = 60.0
NAT_RETRY_INTERVAL_AFTER_FAILURE = 300.0

# Routes that do NOT represent a confirmed direct connection -- retry/
# global-discovery loops must keep trying a peer in any of these states.
# "relay" means we have candidates FROM relay-assisted rendezvous, not a
# verified direct connection yet (2026-08-08, "relay-assisted rendezvous
# and direct-path candidate orchestration") -- treating it as "already
# reachable" would wrongly suppress the real reachability check.
_UNCONFIRMED_ROUTES = (None, "unreachable", "relay")

# Direct-path candidate racing (2026-08-08, "relay-assisted rendezvous and
# direct-path candidate orchestration"): bounded concurrency so a trusted
# device with many known/candidate addresses doesn't open dozens of sockets
# at once, and a bounded per-attempt timeout so one slow/black-holed
# candidate can't stall the whole race.
RACE_MAX_CONCURRENT = 4
RACE_TIMEOUT_PER_ATTEMPT = 5.0
# Happy Eyeballs-style "Connection Attempt Delay" (RFC 8305 section 5) --
# the stagger between successive attempt *starts*, not a per-attempt
# timeout. Zero by default so every existing race_candidates() caller/test
# keeps its exact prior (all-attempts-launch-immediately) behavior;
# connect_to_trusted() opts into a real stagger explicitly for its own
# dual-stack candidate race (2026-08-09, "internal IPv6 direct connectivity
# and dual-stack route selection").
RACE_STAGGER_DELAY = 0.25


class RaceAllFailedError(Exception):
    """Every candidate in a race_candidates() call failed -- carries the
    real per-candidate error for each one, never a single flattened
    message, so a caller (or the native UI's route diagnostics) can show
    exactly why each address didn't work."""

    def __init__(self, errors: dict[str, str]):
        self.errors = dict(errors)
        super().__init__(f"no candidate succeeded: {self.errors}")


async def race_candidates(
    candidate_list: list[str],
    attempt,
    *,
    max_concurrent: int = RACE_MAX_CONCURRENT,
    timeout_per_attempt: float = RACE_TIMEOUT_PER_ATTEMPT,
    stagger_delay: float = 0.0,
):
    """Races `attempt(candidate)` across every candidate in `candidate_list`,
    at most `max_concurrent` in flight at once. Returns `(winning_candidate,
    result)` for the first real success and cancels every still-pending
    attempt immediately -- never leaves a redundant connection attempt
    running after a winner is found. Raises RaceAllFailedError (carrying
    every real per-candidate error, including timeouts) if none succeed.
    Pure orchestration -- `attempt` decides what "success" actually means
    (e.g. a real authenticated direct_tcp connection).

    `stagger_delay` (default 0, i.e. every attempt starts at once): when
    positive, the Nth candidate's attempt is delayed by `N * stagger_delay`
    before it starts (a real Happy Eyeballs-style connection-attempt
    stagger, not a per-attempt timeout) -- a still-delayed candidate that
    gets cancelled before its delay elapses never opens a socket at all, so
    there is nothing to drain for it."""
    if not candidate_list:
        raise RaceAllFailedError({})
    semaphore = asyncio.Semaphore(max_concurrent)
    errors: dict[str, str] = {}

    async def _close_loser(result) -> None:
        """Best-effort close for an attempt that completed successfully but
        lost the race. DirectConnection.close() is async; keeping this
        tolerant also preserves race_candidates()'s generic test surface."""
        close = getattr(result, "close", None)
        if not callable(close):
            return
        try:
            closed = close()
            if inspect.isawaitable(closed):
                await closed
        except Exception:  # noqa: BLE001 -- loser cleanup must not hide the winner
            pass

    async def _try(candidate: str, delay: float):
        if delay:
            await asyncio.sleep(delay)
        async with semaphore:
            return await asyncio.wait_for(attempt(candidate), timeout=timeout_per_attempt)

    tasks = {
        asyncio.ensure_future(_try(candidate, index * stagger_delay)): candidate
        for index, candidate in enumerate(candidate_list)
    }
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            successes: list[tuple[str, object]] = []
            for task in done:
                candidate = tasks[task]
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors[candidate] = str(exc)
                    continue
                successes.append((candidate, result))
            if successes:
                for other in pending:
                    other.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                winner_candidate, winner_result = successes[0]
                for _candidate, loser_result in successes[1:]:
                    await _close_loser(loser_result)
                return winner_candidate, winner_result
        raise RaceAllFailedError(errors)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if not isinstance(result, BaseException):
                await _close_loser(result)
        raise


def _split_candidate_host_port(candidate: str) -> tuple[str, int]:
    """Splits a real, already-normalized `candidates.normalize_candidate()`
    output (`host:port` or `[ipv6]:port`) back into its parts -- never
    called on unnormalized input."""
    if candidate.startswith("["):
        host, _, port = candidate[1:].partition("]:")
        return host, int(port)
    host, _, port = candidate.rpartition(":")
    return host, int(port)


def _local_ipv4_addresses() -> list[str]:
    """Best-effort REAL local IPv4 addresses to advertise via LAN discovery
    -- never fabricated. Returns an empty list if none can be determined
    (a sandboxed/loopback-only environment, for instance), which honestly
    means this system currently has no reachable address to announce, not a
    guessed one."""
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    return sorted(addresses)


def _local_ipv6_addresses() -> list[str]:
    """Best-effort REAL local IPv6 host literals (2026-08-09, "internal
    IPv6 direct connectivity and dual-stack route selection") -- bracketed
    where a caller needs `[addr]:port`, but returned here as bare literals
    (`_own_local_candidates()` adds the port). A real, connectable
    link-local address is included WITH its real numeric scope id
    (`fe80::1%12`) -- see candidates.py's own module docstring for why a
    scope id is host-local and never safe to hand to another device;
    callers that publish candidates off-host must run the result through
    `candidates.strip_unpublishable_candidates()` first. Returns an empty
    list on an IPv6-unavailable system or a purely loopback one -- never
    fabricated. Not deduplicated against IPv4 -- callers merge both."""
    if not direct_tcp.ipv6_supported():
        return []
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET6):
            ip, _port, _flow, scope_id = info[4]
            try:
                parsed = ipaddress.IPv6Address(ip)
            except ValueError:
                continue
            if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast:
                continue
            if parsed.is_link_local:
                if not scope_id:
                    continue
                addresses.add(f"{ip}%{scope_id}")
            else:
                addresses.add(ip)
    except OSError:
        pass
    return sorted(addresses)


def _local_publishable_addresses() -> list[str]:
    """Real local IPv4 + IPv6 bare host literals (no port) safe to hand to
    ANY other device -- excludes a zone-scoped IPv6 link-local literal,
    since a zone id is host-local and never meaningful (or safe) on
    another device, even on the same physical LAN (its own interface index
    is a different, unrelated number). Used for LAN announcement, which
    combines these bare hosts with the real listen port on receipt, not
    before (2026-08-09, "internal IPv6 direct connectivity and dual-stack
    route selection")."""
    return _local_ipv4_addresses() + [addr for addr in _local_ipv6_addresses() if "%" not in addr]


class SyncService:
    """One process-wide instance owns every real socket/task sync ever
    opens. `on_status(event_dict)` is called for real status changes
    (started/stopped/peer_seen/rejected) -- the native UI's job is to
    render exactly these events, never to guess reachability itself."""

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        system_uuid: str | None = None,
        on_status=None,
        on_transfer_offer=None,
        on_metadata_requested=None,
        on_metadata_reconciled=None,
        on_metadata_pull_requested=None,
    ):
        self.base_dir = base_dir
        # Test-only override, same rationale as direct_tcp.load_local_node()'s
        # own system_uuid kwarg: real callers omit this and get
        # pythia.identity.get_system_uuid() (the real, process-global
        # system_uuid) -- passing one lets two SyncService instances in one
        # test process simulate two genuinely different systems, since
        # get_system_uuid() alone is process-global and would otherwise make
        # every in-process SyncService "the same system" (a real
        # SelfConnectionError trap this module's own tests hit once).
        self._system_uuid = system_uuid
        self._on_status = on_status
        # Called (offer_id, offer_dict, peer_system_uuid) the instant a real
        # TRANSFER_OFFER arrives on an authenticated incoming connection --
        # a real, un-decided-yet notification for the UI to show. Never
        # auto-accepted; see resolve_offer() below for the only path that
        # actually decides.
        self._on_transfer_offer = on_transfer_offer
        # Called with (peer_system_uuid), must return the real, previously-
        # mirrored metadata items (list[dict], the same shape
        # metadata.create_record()/create_tombstone() produce) this system
        # already has FROM THAT PEER -- called only when a peer actually
        # pushes a REPOSITORY_SUMMARY to us, never eagerly. Backend supplies
        # this from real datadir mirrored-entry storage; SyncService itself
        # never reads datadir directly (core sync stays storage-agnostic).
        self._on_metadata_requested = on_metadata_requested
        # Called (peer_system_uuid, reconcile_result_dict) after a real
        # reconciliation completes -- the only path that lets the caller
        # (Backend) actually persist accepted upserts/tombstones. SyncService
        # itself never writes to datadir; see metadata_session.py's own
        # docstring for why that separation matters.
        self._on_metadata_reconciled = on_metadata_reconciled
        # Called with no arguments when a peer explicitly asks to PULL our
        # own state (METADATA_PULL_REQUEST) -- must return this system's
        # real, own-published items (never mirrored copies of someone
        # else's data), the same shape _sync_local_metadata_items() already
        # builds for the push path.
        self._on_metadata_pull_requested = on_metadata_pull_requested
        self.node: direct_tcp.LocalNode | None = None
        self._listener = None
        self._listen_port: int | None = None
        self._lan_service: lan.LanDiscoveryService | None = None
        self._lan_last_error: str | None = None
        self._peers: dict[str, dict] = {}
        self._pending_offers: dict[str, dict] = {}
        self._active_transfers: dict[str, dict] = {}
        # Every _on_connected() invocation's own task (2026-08-08, "relay-
        # assisted rendezvous and direct-path candidate orchestration",
        # "shutdown during in-flight work"). A real bug found writing that
        # test: a connection stuck waiting on an unresolved transfer-offer
        # decision (or any other in-progress exchange) is NOT covered by
        # _active_transfers, so stop()'s own `listener.wait_closed()` would
        # hang forever on Python 3.13, which changed Server.wait_closed() to
        # block until every still-running connection handler actually
        # returns. Tracked here so stop() can cancel every one of them
        # before ever awaiting wait_closed().
        self._connection_tasks: set[asyncio.Task] = set()
        self._retry_state: dict[str, dict] = {}
        self._retry_task: asyncio.Task | None = None
        self._global_discovery_task: asyncio.Task | None = None
        self._global_discovery_executor: concurrent.futures.ThreadPoolExecutor | None = None
        # Relay-assisted rendezvous (2026-08-08, "relay-assisted rendezvous
        # and direct-path candidate orchestration"). Control-plane only --
        # see relay.py's own module docstring for the trust model. None
        # while disabled or no relay_server was configured; a real
        # RelayClient only once start() actually connects one.
        self._relay_client: relay.RelayClient | None = None
        self._pending_relay_answers: dict[str, asyncio.Future] = {}
        # Internal NAT traversal (2026-08-08, "internal NAT traversal and
        # direct-route publication"). `_nat_mapping` is the real, currently-
        # active mapping (None while disabled, unsupported everywhere, or
        # not yet attempted); `_nat_last_error` is a real, honest per-
        # protocol failure summary for diagnostics, never fake reachability.
        self._nat_mapping: nat.NatMapping | None = None
        self._nat_gateway: str | None = None
        self._nat_task: asyncio.Task | None = None
        self._nat_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._nat_last_error: str | None = None
        self._nat_upnp_gateway: nat.UpnpGateway | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _emit(self, event: dict) -> None:
        if self._on_status:
            self._on_status(event)

    async def start(
        self,
        *,
        listen_host: str = DEFAULT_LISTEN_HOST,
        listen_port: int = DEFAULT_LISTEN_PORT,
        global_discovery_server: str = "",
        relay_server: str = "",
    ) -> dict:
        cfg = sync_config.load(base_dir=self.base_dir)
        if not cfg["enabled"]:
            return {"status": "disabled"}
        if self._running:
            return {"status": "already_running"}

        # Real side effect, only now that we've confirmed sync is actually
        # enabled: identity creation (writes key.pem/cert.pem/identity.json
        # on first call, matching the task's "device identity creation only
        # after enable confirmation").
        self.node = direct_tcp.load_local_node(base_dir=self.base_dir, system_uuid=self._system_uuid)

        # A real bug found while regression-testing this delivery
        # (2026-08-09, "internal IPv6 direct connectivity and dual-stack
        # route selection"): if the listener bound successfully but a LATER
        # step here (LAN discovery's own UDP bind, most likely to fail --
        # its fixed port can genuinely collide with another real LAN
        # discovery service already running on this host) raised, `_running`
        # was never set True, so stop()'s own `if not self._running: return`
        # guard skipped closing the listener entirely -- a real socket leak
        # on partial-start failure, now doubled in impact by the dual-stack
        # listener holding two sockets for one port instead of one. Any
        # failure from here on must clean up whatever was already opened
        # before re-raising, never leave it dangling.
        try:
            if cfg["listen"]:
                self._listener = await direct_tcp.start_listener(
                    listen_host, listen_port, self.node, self._on_connected, on_reject=self._on_rejected
                )
                self._listen_port = listen_port

            if cfg["lan_discovery"]:
                try:
                    self._lan_service = await lan.start(
                        self.node,
                        direct_port=self._listen_port or 0,
                        addresses=_local_publishable_addresses(),
                        on_peer=self._on_lan_peer,
                        base_dir=self.base_dir,
                    )
                    self._lan_last_error = None
                except OSError as exc:
                    self._lan_last_error = str(exc)
                    self._emit({"event": "lan_discovery_error", "error": str(exc)})
        except BaseException:
            # Mirrors stop()'s own established cleanup order (cancel any
            # tracked connection task, close LAN, THEN close+await the
            # listener) -- Python 3.13's `Server.wait_closed()` waits for
            # every still-running connection handler, which can hang
            # indefinitely for one stuck on an unresolved application-level
            # exchange (see _connection_tasks' own docstring). Also bounded
            # by a hard timeout as defense-in-depth: a partial-start failure
            # must never turn into an unbounded hang, even if some future
            # change to this cleanup sequence reintroduces a real wait.
            async def _cleanup() -> None:
                remaining_connections = list(self._connection_tasks)
                for task in remaining_connections:
                    task.cancel()
                if remaining_connections:
                    await asyncio.gather(*remaining_connections, return_exceptions=True)
                self._connection_tasks.clear()
                if self._lan_service is not None:
                    await self._lan_service.close()
                    self._lan_service = None
                if self._listener is not None:
                    self._listener.close()
                    await self._listener.wait_closed()
                    self._listener = None
                    self._listen_port = None

            try:
                await asyncio.wait_for(_cleanup(), timeout=15.0)
            except asyncio.TimeoutError:
                pass
            raise

        self._running = True
        self._retry_task = asyncio.ensure_future(self._retry_loop())
        if cfg["global_discovery"] and global_discovery_server:
            self._global_discovery_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="pythia-global-discovery"
            )
            self._global_discovery_task = asyncio.ensure_future(self._global_discovery_loop(global_discovery_server))
        if cfg["relay_discovery"] and relay_server:
            normalized = candidates_module.normalize_candidate(relay_server)
            if normalized is not None:
                host, port = _split_candidate_host_port(normalized)
                client = relay.RelayClient(
                    self.node,
                    on_candidate_offer=self._on_relay_candidate_offer,
                    on_candidate_answer=self._on_relay_candidate_answer,
                )
                try:
                    await client.connect(host, port)
                    self._relay_client = client
                    self._emit({"event": "relay_connected", "server": relay_server})
                except Exception as exc:
                    self._emit({"event": "relay_connect_error", "server": relay_server, "error": str(exc)})
        if cfg["nat_traversal"] and cfg["listen"] and self._listener is not None:
            self._nat_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pythia-nat")
            self._nat_task = asyncio.ensure_future(self._nat_loop())
        self._emit({"event": "started", "device_id": self.node.device_id, "system_uuid": self.node.system_uuid})
        return {
            "status": "partial" if self._lan_last_error else "ok",
            "device_id": self.node.device_id,
            "system_uuid": self.node.system_uuid,
            "lan_discovery_error": self._lan_last_error,
        }

    async def stop(self) -> dict:
        if not self._running:
            return {"status": "not_running"}
        for task in (self._retry_task, self._global_discovery_task, self._nat_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._retry_task = None
        self._global_discovery_task = None
        self._nat_task = None
        if self._nat_mapping is not None and self._nat_executor is not None:
            # Real, best-effort deletion (2026-08-08, "delete the mapping on
            # disable/stop or relevant live configuration change") -- a
            # failure here is not fatal, the mapping will simply expire on
            # its own, but we always try so a well-behaved router's table
            # doesn't carry a stale entry for a listener that's about to
            # stop existing.
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(self._nat_executor, self._delete_nat_mapping_blocking)
            except Exception:
                pass
        self._nat_mapping = None
        self._nat_gateway = None
        self._nat_upnp_gateway = None
        if self._nat_executor is not None:
            self._nat_executor.shutdown(wait=True, cancel_futures=True)
            self._nat_executor = None
        if self._relay_client is not None:
            await self._relay_client.close()
            self._relay_client = None
        for future in self._pending_relay_answers.values():
            if not future.done():
                future.cancel()
        self._pending_relay_answers.clear()
        if self._global_discovery_executor is not None:
            # Cancelling an asyncio Future does not stop urllib already running
            # inside its worker thread. Wait for those bounded-timeout requests
            # before reporting sync disabled, then reject any queued calls.
            self._global_discovery_executor.shutdown(wait=True, cancel_futures=True)
            self._global_discovery_executor = None
        active = list(self._active_transfers.values())
        for entry in active:
            try:
                await entry["connection"].send_json(
                    protocol.MessageType.TRANSFER_CANCEL,
                    {"message": "Sync was disabled."},
                    stream_id=entry["stream_id"],
                )
            except Exception:
                pass
            entry["task"].cancel()
        if active:
            await asyncio.gather(*(entry["task"] for entry in active), return_exceptions=True)
            for entry in active:
                try:
                    await entry["connection"].close()
                except Exception:
                    pass
        self._active_transfers.clear()
        # Cancel every remaining in-progress connection handler (pending
        # transfer-offer decisions, in-flight metadata exchanges, etc.)
        # BEFORE closing/awaiting the listener -- see _connection_tasks'
        # own docstring for the real Python 3.13 hang this fixes. Each
        # task's own try/finally already guarantees its connection gets
        # closed even when cancelled (see _on_connected()).
        remaining_connections = list(self._connection_tasks)
        for task in remaining_connections:
            task.cancel()
        if remaining_connections:
            await asyncio.gather(*remaining_connections, return_exceptions=True)
        self._connection_tasks.clear()
        if self._lan_service is not None:
            await self._lan_service.close()
            self._lan_service = None
        if self._listener is not None:
            self._listener.close()
            await self._listener.wait_closed()
            self._listener = None
        self._running = False
        self._emit({"event": "stopped"})
        return {"status": "ok"}

    async def _on_connected(self, connection: direct_tcp.DirectConnection) -> None:
        """Real entry point direct_tcp.start_listener() calls directly --
        this IS the per-connection task the server itself awaits, so
        tracking `asyncio.current_task()` here lets stop() cancel it
        externally and unblocks a Python 3.13 `Server.wait_closed()` that
        would otherwise wait for it forever (see _connection_tasks'
        docstring). Guarantees the connection is closed exactly once no
        matter which path _handle_connection() takes, including
        cancellation -- _handle_connection()'s own branches already close it
        in most cases, but the pending-transfer-offer-decision wait did not
        (a real, separate bug this same investigation found); the outer
        `finally` here covers every case uniformly instead of relying on
        each branch to remember."""
        task = asyncio.current_task()
        if task is not None:
            self._connection_tasks.add(task)
        try:
            await self._handle_connection(connection)
        finally:
            if task is not None:
                self._connection_tasks.discard(task)
            await connection.close()

    async def _handle_connection(self, connection: direct_tcp.DirectConnection) -> None:
        """A real incoming connection was authenticated. Branches on the
        first frame's real type: a TRANSFER_OFFER is surfaced to the UI via
        on_transfer_offer() and genuinely waits (up to 120s) for
        resolve_offer() to be called with the user's real decision -- never
        auto-accepted, and a timeout with no response is a real reject, not a
        fake success. A REPOSITORY_SUMMARY means a peer is pushing its own
        metadata to us (see pythia.ui.sync_bridge.sync_metadata_with_device(),
        the offering-side caller); we reconcile it against on_metadata_requested()'s
        real local items and hand the classification to on_metadata_reconciled()
        for the caller to actually persist -- SyncService itself never writes
        to datadir. A METADATA_PULL_REQUEST means a peer is explicitly asking
        to pull OUR OWN state (see metadata_session.pull_and_reconcile()) --
        answered via on_metadata_pull_requested(). Anything else (a plain
        control ping, or nothing) just closes the connection."""
        self._record_peer(connection.peer.system_uuid, route="direct_incoming")
        try:
            frame = await connection.recv_frame(timeout=30)
        except Exception:
            await connection.close()
            return

        if frame.message_type == protocol.MessageType.REPOSITORY_SUMMARY:
            await self._handle_incoming_metadata_summary(connection, frame)
            return

        if frame.message_type == protocol.MessageType.METADATA_PULL_REQUEST:
            await self._handle_metadata_pull_request(connection, frame)
            return

        if frame.message_type != protocol.MessageType.TRANSFER_OFFER:
            await connection.close()
            return

        offer = frame.json() or {}
        offer_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_offers[offer_id] = {"connection": connection, "offer": offer, "peer": connection.peer, "future": future}
        if self._on_transfer_offer:
            self._on_transfer_offer(offer_id, dict(offer), connection.peer.system_uuid)

        try:
            decision = await asyncio.wait_for(future, timeout=120)
        except asyncio.TimeoutError:
            decision = None
        finally:
            self._pending_offers.pop(offer_id, None)

        if not decision or not decision.get("accept"):
            try:
                await transfer_offer.reject_offer(
                    connection, message=(decision or {}).get("message") or "No response.", stream_id=frame.stream_id
                )
            except Exception:
                pass
            await connection.close()
            return

        system_uuid = connection.peer.system_uuid
        # A real, separately-cancellable task (2026-08-08, Codex sync review,
        # "Add caller-driven transfer cancellation") -- tracked so
        # cancel_transfer() can interrupt it from outside this coroutine's
        # own await chain, independent of the peer's own behavior.
        task = asyncio.ensure_future(
            transfer_offer.accept_offer_and_receive_game(
                connection, decision["destination"], decision["cache"], stream_id=frame.stream_id
            )
        )
        self._active_transfers[system_uuid] = {"task": task, "connection": connection, "stream_id": frame.stream_id}
        try:
            result = await task
            self._emit({"event": "transfer_received", "system_uuid": system_uuid, "result": result})
        except asyncio.CancelledError:
            try:
                await connection.send_json(
                    protocol.MessageType.TRANSFER_CANCEL, {"message": "Cancelled by local user."}, stream_id=frame.stream_id
                )
            except Exception:
                pass
            self._emit({"event": "transfer_cancelled", "system_uuid": system_uuid})
        except Exception as exc:
            self._emit({"event": "transfer_error", "system_uuid": system_uuid, "error": str(exc)})
        finally:
            self._active_transfers.pop(system_uuid, None)
            await connection.close()

    async def cancel_transfer(self, system_uuid: str) -> bool:
        """Caller-driven cancellation of a currently-RECEIVING transfer from
        `system_uuid` (2026-08-08, Codex sync review). Sends a real
        TRANSFER_CANCEL to the peer first (best-effort -- the peer's own
        send_game() already watches for exactly this frame and raises
        TransferError, matching pre-existing peer-driven-cancel behavior),
        then cancels our own local task so it stops waiting/looping
        immediately rather than only reacting to the peer's own next frame.
        Already-received blocks stay in the real block cache
        (transfer_session.store_block() already wrote them incrementally,
        materialize() never ran) -- a later retry reuses them via the
        existing resume/cache-reuse path, unchanged. Returns False if no
        transfer is currently active for that peer."""
        entry = self._active_transfers.get(system_uuid)
        if entry is None:
            return False
        try:
            await entry["connection"].send_json(
                protocol.MessageType.TRANSFER_CANCEL, {"message": "Cancelled by local user."}, stream_id=entry["stream_id"]
            )
        except Exception:
            pass
        entry["task"].cancel()
        return True

    async def _handle_incoming_metadata_summary(self, connection: direct_tcp.DirectConnection, frame) -> None:
        """Real receiving-role continuation for a peer-initiated metadata
        push. Silently closes (no real op) if the caller never wired
        on_metadata_requested() -- an incoming metadata push is meaningless
        without knowing our own local items, and refusing is honest, not a
        fake success.

        Passes the real, authenticated peer system_uuid to
        on_metadata_requested() (2026-08-08, Codex sync review: "correctness
        repair required") -- the caller must return this peer's own
        previously-mirrored records/tombstones here, not merely this
        system's own entries, or metadata.reconcile() has no prior state to
        compare an incoming push's revision against and will always treat it
        as new."""
        if self._on_metadata_requested is None:
            await connection.close()
            return
        try:
            local_items = self._on_metadata_requested(connection.peer.system_uuid) or []
            remote_summary = frame.json() or {}
            result = await metadata_session.reconcile_from_summary(
                connection,
                remote_summary,
                local_items,
                peer_system_uuid=connection.peer.system_uuid,
                stream_id=frame.stream_id,
                timeout=30,
            )
            if self._on_metadata_reconciled:
                self._on_metadata_reconciled(connection.peer.system_uuid, result)
            self._emit({"event": "metadata_synced", "system_uuid": connection.peer.system_uuid, "result": result})
        except Exception as exc:
            self._emit({"event": "metadata_sync_error", "system_uuid": connection.peer.system_uuid, "error": str(exc)})
        finally:
            await connection.close()

    async def _handle_metadata_pull_request(self, connection: direct_tcp.DirectConnection, frame) -> None:
        """Real offering-role response to a peer's explicit
        METADATA_PULL_REQUEST (2026-08-08, Codex sync review, "make one user
        action genuinely bidirectional ... or provide explicit separate
        Push/Pull actions"). Publishes only what on_metadata_pull_requested()
        returns (this system's own real state) -- never a mirrored copy of
        someone else's data. Silently closes if the hook isn't wired,
        matching _handle_incoming_metadata_summary()'s own honest-refusal
        pattern."""
        if self._on_metadata_pull_requested is None:
            await connection.close()
            return
        try:
            own_items = self._on_metadata_pull_requested() or []
            result = await metadata_session.send_summary_and_records(
                connection, own_items, stream_id=frame.stream_id, timeout=30
            )
            self._emit({"event": "metadata_pull_served", "system_uuid": connection.peer.system_uuid, "result": result})
        except Exception as exc:
            self._emit({"event": "metadata_sync_error", "system_uuid": connection.peer.system_uuid, "error": str(exc)})
        finally:
            await connection.close()

    def resolve_offer(self, offer_id: str, *, accept: bool, destination: str | None = None, cache: str | None = None, message: str = "") -> bool:
        """The only path that actually decides a pending incoming offer --
        callable from any thread (the caller is expected to route this
        through the same runtime the service's event loop lives on).
        Returns False if `offer_id` is unknown (already resolved, timed
        out, or never existed) rather than raising, since a caller racing a
        timeout is a real, expected case, not a bug."""
        pending = self._pending_offers.get(offer_id)
        if pending is None:
            return False
        future = pending["future"]
        loop = future.get_loop()

        def _set() -> None:
            if not future.done():
                future.set_result({"accept": accept, "destination": destination, "cache": cache, "message": message})

        loop.call_soon_threadsafe(_set)
        return True

    def pending_offers(self) -> dict:
        return {
            offer_id: {"offer": dict(p["offer"]), "peer_system_uuid": p["peer"].system_uuid}
            for offer_id, p in self._pending_offers.items()
        }

    def _on_rejected(self, exc: Exception) -> None:
        self._emit({"event": "rejected", "error": str(exc)})

    def _on_lan_peer(self, candidate: dict) -> None:
        """A real bug found while building the candidate racer (2026-08-08,
        "relay-assisted rendezvous and direct-path candidate
        orchestration"): `discovery.parse_announcement()`'s real `port`
        field was silently discarded here -- only the bare host addresses
        were ever persisted, so connect_to_trusted()'s own candidate racer
        would have had to guess a port for every LAN-discovered address.
        Now combines each bare host with the peer's real announced port
        into a normalized `host:port` candidate before persisting."""
        system_uuid = candidate.get("system_uuid")
        if not system_uuid:
            return
        raw_addresses = candidate.get("addresses") or []
        port = candidate.get("port")
        combined: list[str] = []
        if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
            for address in raw_addresses:
                normalized = candidates_module.normalize_candidate(f"{address}:{port}")
                if normalized:
                    combined.append(normalized)
        addresses = combined or raw_addresses
        if addresses:
            trust.update_known_addresses(system_uuid, addresses, base_dir=self.base_dir)
        self._record_peer(system_uuid, route="lan", addresses=addresses)

    def _record_peer(self, system_uuid: str, *, route: str, addresses: list[str] | None = None, error: str | None = None) -> None:
        entry = self._peers.setdefault(system_uuid, {"addresses": []})
        entry["last_seen"] = time.time()
        entry["route"] = route
        if addresses:
            entry["addresses"] = addresses
        entry["error"] = error
        self._emit({"event": "peer_seen", "system_uuid": system_uuid, **entry})

    def known_peers(self) -> dict:
        """A snapshot of every peer this service has actually observed or
        attempted to reach this run -- real last_seen/route/error, never
        guessed reachability."""
        return {uuid: dict(info) for uuid, info in self._peers.items()}

    async def connect_to_trusted(self, system_uuid: str, *, port: int) -> direct_tcp.DirectConnection:
        """Races every known candidate address for a trusted device
        (2026-08-08, "relay-assisted rendezvous and direct-path candidate
        orchestration" -- upgraded from a sequential in-order loop to
        bounded-concurrent racing via race_candidates(), same public
        signature so every existing caller/test keeps working unchanged).
        Local/private candidates are ranked first. Stored addresses are a
        real historical mix -- some already carry their own port (pairing,
        global discovery), some are bare hosts (older LAN-discovered
        records, before _on_lan_peer() started combining the announced
        port); `port` is only ever used as the fallback for a bare host,
        never to override an address that already has its own port.
        Records the real outcome (winning route, or every real
        per-candidate error) either way -- never silently returns a
        placeholder connection."""
        if self.node is None:
            raise direct_tcp.DirectConnectError("Sync is not running -- call start() first.")
        record = trust.get_trusted(system_uuid, base_dir=self.base_dir)
        if record is None:
            raise direct_tcp.UnknownDeviceError(f"{system_uuid} is not a trusted device.")
        raw_addresses = record.get("known_addresses") or []
        with_ports = [
            addr if candidates_module.normalize_candidate(addr) else f"{addr}:{port}" for addr in raw_addresses
        ]
        # rank_and_interleave(), not merge_and_rank(): still prefer a real
        # local/direct candidate over a public one, but interleave IPv6/IPv4
        # within each of those groups rather than exhausting one family
        # first (2026-08-09, "internal IPv6 direct connectivity and
        # dual-stack route selection", Happy Eyeballs-style racing).
        ranked = candidates_module.rank_and_interleave(with_ports)

        async def _attempt(candidate: str) -> direct_tcp.DirectConnection:
            host, candidate_port = _split_candidate_host_port(candidate)
            return await direct_tcp.connect(host, candidate_port, self.node, expected_system_uuid=system_uuid)

        errors: dict[str, str] = {}
        if ranked:
            try:
                winning_candidate, connection = await race_candidates(ranked, _attempt, stagger_delay=RACE_STAGGER_DELAY)
                self._record_peer(system_uuid, route="direct_outgoing", addresses=[winning_candidate])
                return connection
            except RaceAllFailedError as exc:
                errors.update(exc.errors)

        # Every known candidate failed (or there were none at all) -- if
        # relay-assisted rendezvous is connected, ask the peer for its
        # current addresses and race those too before giving up honestly.
        if self._relay_client is not None and self._relay_client.connected:
            try:
                fresh = await self.request_relay_candidates(system_uuid)
            except Exception as exc:
                fresh = []
                errors["relay"] = str(exc)
            if fresh:
                retried = candidates_module.rank_and_interleave(fresh)
                try:
                    winning_candidate, connection = await race_candidates(retried, _attempt, stagger_delay=RACE_STAGGER_DELAY)
                    self._record_peer(system_uuid, route="direct_outgoing", addresses=[winning_candidate])
                    return connection
                except RaceAllFailedError as exc:
                    errors.update(exc.errors)

        detail = "; ".join(f"{candidate}: {error}" for candidate, error in errors.items()) or "No known address for this device yet."
        self._record_peer(system_uuid, route="unreachable", error=detail)
        raise direct_tcp.DirectConnectError(f"Could not reach {system_uuid}: {detail}")

    def dual_stack_status(self) -> dict:
        """Real, honest dual-stack/IPv6 diagnostic snapshot for the native
        UI (2026-08-09, "internal IPv6 direct connectivity and dual-stack
        route selection") -- never guessed. `listening_families` reflects
        what the listener actually bound (direct_tcp.listener_families()),
        which can legitimately end up IPv4-only even after a dual-stack
        bind was attempted (IPv6 unavailable on this system) or if the user
        set an explicit IPv4-only listen_host. `ipv6_supported` is a real,
        cached socket capability probe, independent of whether we're
        currently listening at all -- lets the UI distinguish "IPv6 isn't
        available here" from "IPv6 is available but we're just not
        listening on it right now"."""
        families = direct_tcp.listener_families(self._listener) if self._listener is not None else []
        own_ipv6 = [c for c in self.own_direct_candidates() if c.startswith("[")]
        return {
            "ipv6_supported": direct_tcp.ipv6_supported(),
            "listening_families": families,
            "own_ipv6_candidates": own_ipv6,
        }

    def _real_listen_port(self) -> int | None:
        """The listener's real, OS-bound port -- never `self._listen_port`
        alone, which stores the *requested* port (0 for an OS-assigned
        ephemeral port, real in every test in this suite that binds
        `listen_port=0` to avoid port conflicts). Returns None if not
        currently listening."""
        if self._listener is None:
            return None
        try:
            return self._listener.sockets[0].getsockname()[1]
        except (IndexError, AttributeError, OSError):
            return self._listen_port or None

    def _own_relay_candidates(self) -> list[str]:
        """This system's own real, currently-listening local addresses --
        what we can honestly offer a peer via relay rendezvous. Empty if
        we're not actually listening (never fabricates a reachable address).
        Includes the real NAT-mapped external candidate (2026-08-08,
        "internal NAT traversal and direct-route publication") if one is
        currently active, ranked alongside the local candidates by
        candidates.merge_and_rank(). IPv4 and IPv6 (2026-08-09, "internal
        IPv6 direct connectivity and dual-stack route selection") -- always
        run through strip_unpublishable_candidates() last, since this list
        is handed to another device (relay offer, global-discovery
        announce, pairing invitation) and a zone-scoped link-local
        candidate is never safe to publish off-host."""
        real_port = self._real_listen_port()
        if not real_port:
            return []
        local_v4 = [f"{addr}:{real_port}" for addr in _local_ipv4_addresses()]
        local_v6 = [f"[{addr}]:{real_port}" for addr in _local_ipv6_addresses()]
        merged = candidates_module.merge_and_rank(local_v4, local_v6, self._nat_mapped_candidate())
        return candidates_module.strip_unpublishable_candidates(merged)

    def _nat_mapped_candidate(self) -> list[str]:
        """The real, currently-active NAT-mapped external `host:port`
        candidate, if any -- already validated as a real publishable
        address by nat.py itself (wildcard/loopback/private/documentation
        addresses are rejected before a NatMapping is ever created)."""
        mapping = self._nat_mapping
        if mapping is None or mapping.external_address is None:
            return []
        return [f"{mapping.external_address}:{mapping.external_port}"]

    def own_direct_candidates(self) -> list[str]:
        """Public accessor for this system's own real, currently-reachable
        `host:port` candidates -- local interfaces plus the real NAT-mapped
        external address if one is active. Used for pairing invitations as
        well as relay rendezvous (_own_relay_candidates() is this same real
        data, kept as a private alias for its own established call sites)."""
        return self._own_relay_candidates()

    def _on_relay_candidate_offer(self, payload: dict) -> None:
        """A trusted peer, via the relay, asked to exchange candidates with
        us. Verifies the offer's real signature/trust/freshness first (see
        relay.verify_candidate_payload()) -- a forged or untrusted offer is
        rejected and never acted on. Auto-responds with our own real,
        currently-listening addresses, the standard offer/answer exchange
        this rendezvous protocol relies on (see request_relay_candidates())."""
        from_uuid = payload.get("from_system_uuid")
        try:
            peer_candidates = relay.verify_candidate_payload(payload, expected_from_system_uuid=from_uuid, base_dir=self.base_dir)
        except relay.RelayAuthError as exc:
            self._emit({"event": "relay_offer_rejected", "system_uuid": from_uuid, "error": str(exc)})
            return
        trust.update_known_addresses(from_uuid, peer_candidates, base_dir=self.base_dir)
        self._record_peer(from_uuid, route="relay", addresses=peer_candidates)
        own = self._own_relay_candidates()
        if own and self._relay_client is not None:
            asyncio.ensure_future(self._relay_client.send_candidate_answer(from_uuid, own))

    def _on_relay_candidate_answer(self, payload: dict) -> None:
        """The real response to our own request_relay_candidates() call --
        verified the same way an offer is (signature/trust/freshness), then
        resolves the matching pending Future if one is still waiting."""
        from_uuid = payload.get("from_system_uuid")
        try:
            peer_candidates = relay.verify_candidate_payload(payload, expected_from_system_uuid=from_uuid, base_dir=self.base_dir)
        except relay.RelayAuthError as exc:
            self._emit({"event": "relay_answer_rejected", "system_uuid": from_uuid, "error": str(exc)})
            return
        trust.update_known_addresses(from_uuid, peer_candidates, base_dir=self.base_dir)
        self._record_peer(from_uuid, route="relay", addresses=peer_candidates)
        future = self._pending_relay_answers.get(from_uuid)
        if future is not None and not future.done():
            future.set_result(peer_candidates)

    async def request_relay_candidates(self, system_uuid: str, *, timeout: float = 10.0) -> list[str]:
        """Real relay-assisted candidate exchange: sends `system_uuid` our
        own current addresses (a real offer -- so they can try reaching us
        too) and waits for their real, signed answer with theirs. Raises if
        relay isn't connected, if we have no real address to offer, or if
        no answer arrives within `timeout` -- never fabricates candidates."""
        if self._relay_client is None or not self._relay_client.connected:
            raise relay.RelayError("relay-assisted rendezvous is not connected")
        own = self._own_relay_candidates()
        if not own:
            raise relay.RelayError("no real local address to offer via relay -- not currently listening")
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_relay_answers[system_uuid] = future
        try:
            await self._relay_client.send_candidate_offer(system_uuid, own)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_relay_answers.pop(system_uuid, None)

    async def set_relay_enabled(self, enabled: bool, *, relay_server: str = "") -> dict:
        """Real, targeted enable/disable of relay-assisted rendezvous alone
        -- no listener/LAN restart (2026-08-08, Codex sync review item 5,
        "Make live capability changes apply safely without restarting
        unrelated sync components where practical"). A real no-op if
        already in the requested state, and a real, honest error (never
        raised) if `relay_server` is missing/malformed when enabling."""
        if not self._running:
            return {"status": "not_running"}
        if enabled:
            if self._relay_client is not None and self._relay_client.connected:
                return {"status": "already_connected"}
            normalized = candidates_module.normalize_candidate(relay_server) if relay_server else None
            if normalized is None:
                return {"status": "error", "message": "relay_server is not a valid host:port"}
            host, port = _split_candidate_host_port(normalized)
            client = relay.RelayClient(
                self.node, on_candidate_offer=self._on_relay_candidate_offer, on_candidate_answer=self._on_relay_candidate_answer
            )
            try:
                await client.connect(host, port)
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
            self._relay_client = client
            self._emit({"event": "relay_connected", "server": relay_server})
            return {"status": "ok"}
        if self._relay_client is not None:
            await self._relay_client.close()
            self._relay_client = None
            self._emit({"event": "relay_disconnected"})
        return {"status": "ok"}

    async def set_global_discovery_enabled(self, enabled: bool, *, global_discovery_server: str = "") -> dict:
        """Real, targeted enable/disable of global discovery alone -- same
        rationale as set_relay_enabled() above."""
        if not self._running:
            return {"status": "not_running"}
        if enabled:
            if self._global_discovery_task is not None:
                return {"status": "already_running"}
            if not global_discovery_server:
                return {"status": "error", "message": "global_discovery_server is not configured"}
            self._global_discovery_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="pythia-global-discovery"
            )
            self._global_discovery_task = asyncio.ensure_future(self._global_discovery_loop(global_discovery_server))
            return {"status": "ok"}
        if self._global_discovery_task is not None:
            self._global_discovery_task.cancel()
            try:
                await self._global_discovery_task
            except asyncio.CancelledError:
                pass
            self._global_discovery_task = None
        if self._global_discovery_executor is not None:
            self._global_discovery_executor.shutdown(wait=True, cancel_futures=True)
            self._global_discovery_executor = None
        return {"status": "ok"}

    # ---- internal NAT traversal (2026-08-08, "internal NAT traversal and
    # direct-route publication") ------------------------------------------------

    def _acquire_nat_mapping_blocking(self) -> None:
        """Real, synchronous PCP -> NAT-PMP -> UPnP fallback -- runs inside
        `_nat_executor`'s single worker thread, never the event loop.
        Raises with every real per-protocol error if all three fail; never
        fabricates a mapping. Reuses the existing PCP nonce / UPnP gateway
        across calls so renewal is a real renewal, not a fresh mapping each
        time."""
        internal_port = self._real_listen_port()
        if not internal_port:
            raise nat.NatError("not currently listening -- nothing to map")
        errors: dict[str, str] = {}

        gateway = self._nat_gateway or nat.detect_default_gateway()
        if gateway:
            self._nat_gateway = gateway
            existing_pcp_nonce = (
                self._nat_mapping.nonce if self._nat_mapping and self._nat_mapping.protocol_name == "pcp" else None
            )
            try:
                self._nat_mapping = nat.pcp_request_mapping(
                    gateway,
                    internal_port=internal_port,
                    ip_protocol="tcp",
                    nonce=existing_pcp_nonce,
                    lifetime=NAT_REQUESTED_LIFETIME,
                )
                return
            except Exception as exc:
                errors["pcp"] = str(exc)

            try:
                external_address, _epoch = nat.natpmp_get_external_address(gateway)
                mapping = nat.natpmp_request_mapping(
                    gateway, internal_port=internal_port, ip_protocol="tcp", lifetime=NAT_REQUESTED_LIFETIME
                )
                mapping.external_address = external_address
                self._nat_mapping = mapping
                return
            except Exception as exc:
                errors["nat-pmp"] = str(exc)
        else:
            errors["gateway"] = "could not detect a default gateway"

        try:
            upnp_gateway = self._nat_upnp_gateway or nat.discover_upnp_gateway()
            if upnp_gateway is None:
                raise nat.NatUnsupportedError("no UPnP Internet Gateway Device found")
            self._nat_upnp_gateway = upnp_gateway
            if gateway:
                internal_client = nat._local_ipv4_for_gateway(gateway)
            else:
                internal_client = socket.gethostbyname(socket.gethostname())
            nat.upnp_add_port_mapping(
                upnp_gateway,
                external_port=internal_port,
                internal_port=internal_port,
                internal_client=internal_client,
                ip_protocol="tcp",
                lease_duration=0,
                description="Pythia",
            )
            external_address = nat.upnp_get_external_ip_address(upnp_gateway)
            self._nat_mapping = nat.NatMapping(
                protocol_name="upnp",
                external_address=external_address,
                external_port=internal_port,
                internal_port=internal_port,
                ip_protocol="tcp",
                lifetime_seconds=NAT_REQUESTED_LIFETIME,
                obtained_at=time.time(),
            )
            return
        except Exception as exc:
            errors["upnp"] = str(exc)

        raise nat.NatError(f"no NAT traversal protocol succeeded: {errors}")

    def _delete_nat_mapping_blocking(self) -> None:
        mapping = self._nat_mapping
        if mapping is None:
            return
        if mapping.protocol_name == "pcp" and self._nat_gateway:
            nat.pcp_delete_mapping(self._nat_gateway, mapping)
        elif mapping.protocol_name == "nat-pmp" and self._nat_gateway:
            nat.natpmp_delete_mapping(self._nat_gateway, internal_port=mapping.internal_port, ip_protocol=mapping.ip_protocol)
        elif mapping.protocol_name == "upnp" and self._nat_upnp_gateway:
            nat.upnp_delete_port_mapping(
                self._nat_upnp_gateway, external_port=mapping.external_port, ip_protocol=mapping.ip_protocol
            )

    async def _nat_loop(self) -> None:
        """Real acquire-then-renew loop -- renews at half the granted
        lifetime (never waiting until the last moment), and retries after a
        bounded interval on real failure. Cancelled cleanly by stop() (see
        stop()'s own NAT cleanup, which also deletes the real mapping)."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    await loop.run_in_executor(self._nat_executor, self._acquire_nat_mapping_blocking)
                    mapping = self._nat_mapping
                    self._nat_last_error = None
                    self._emit(
                        {
                            "event": "nat_mapping_acquired",
                            "protocol": mapping.protocol_name,
                            "external_address": mapping.external_address,
                            "external_port": mapping.external_port,
                            "lifetime": mapping.lifetime_seconds,
                        }
                    )
                    delay = max(NAT_MIN_RENEW_INTERVAL, mapping.lifetime_seconds * NAT_RENEW_FRACTION)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._nat_mapping = None
                    self._nat_last_error = str(exc)
                    self._emit({"event": "nat_mapping_error", "error": str(exc)})
                    delay = NAT_RETRY_INTERVAL_AFTER_FAILURE
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise

    def nat_status(self) -> dict:
        """Real, honest NAT-mapping status for native UI diagnostics --
        protocol, external endpoint, lease/renewal, last error -- never
        fake reachability."""
        mapping = self._nat_mapping
        return {
            "active": mapping is not None,
            "protocol": mapping.protocol_name if mapping else None,
            "external_address": mapping.external_address if mapping else None,
            "external_port": mapping.external_port if mapping else None,
            "lifetime_seconds": mapping.lifetime_seconds if mapping else None,
            "obtained_at": mapping.obtained_at if mapping else None,
            "last_error": self._nat_last_error,
        }

    async def retry_nat_mapping_now(self) -> dict:
        """Explicit, user-triggered retry -- runs the same real acquire
        logic immediately rather than waiting for the loop's own retry
        interval. A real no-op if NAT traversal isn't running at all."""
        if self._nat_task is None or self._nat_executor is None:
            return {"status": "not_running"}
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(self._nat_executor, self._acquire_nat_mapping_blocking)
        except Exception as exc:
            self._nat_mapping = None
            self._nat_last_error = str(exc)
            self._emit({"event": "nat_mapping_error", "error": str(exc)})
            return {"status": "error", "message": str(exc)}
        mapping = self._nat_mapping
        self._nat_last_error = None
        self._emit(
            {
                "event": "nat_mapping_acquired",
                "protocol": mapping.protocol_name,
                "external_address": mapping.external_address,
                "external_port": mapping.external_port,
                "lifetime": mapping.lifetime_seconds,
            }
        )
        return {"status": "ok"}

    async def set_nat_traversal_enabled(self, enabled: bool) -> dict:
        """Real, targeted enable/disable of NAT traversal alone -- no
        listener/LAN restart, same rationale as set_relay_enabled()/
        set_global_discovery_enabled() above."""
        if not self._running:
            return {"status": "not_running"}
        if enabled:
            if self._nat_task is not None:
                return {"status": "already_running"}
            if self._listener is None:
                return {"status": "error", "message": "not currently listening -- nothing to map"}
            self._nat_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pythia-nat")
            self._nat_task = asyncio.ensure_future(self._nat_loop())
            return {"status": "ok"}
        if self._nat_task is not None:
            self._nat_task.cancel()
            try:
                await self._nat_task
            except asyncio.CancelledError:
                pass
            self._nat_task = None
        if self._nat_mapping is not None and self._nat_executor is not None:
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(self._nat_executor, self._delete_nat_mapping_blocking)
            except Exception:
                pass
        self._nat_mapping = None
        self._nat_gateway = None
        self._nat_upnp_gateway = None
        if self._nat_executor is not None:
            self._nat_executor.shutdown(wait=True, cancel_futures=True)
            self._nat_executor = None
        return {"status": "ok"}

    def retry_state(self) -> dict:
        """Real, honest next-attempt/attempts-so-far per trusted peer
        currently in backoff -- never guessed reachability (2026-08-08,
        Codex sync review, "expose next-attempt/last-error honestly")."""
        return {uuid: dict(state) for uuid, state in self._retry_state.items()}

    async def _retry_loop(self) -> None:
        """Bounded exponential backoff for trusted peers with a known
        address that are not currently reachable -- checks every
        RETRY_CHECK_INTERVAL seconds, only actually re-attempts a peer once
        its own backoff window has elapsed, and stops attempting a given
        peer after RETRY_MAX_ATTEMPTS (never an infinite hot loop). Cancelled
        cleanly by stop(); a probe connection is always closed immediately
        -- this loop only tests reachability, it never initiates a transfer
        or metadata exchange itself."""
        try:
            while True:
                await asyncio.sleep(RETRY_CHECK_INTERVAL)
                now = time.time()
                for record in trust.list_trusted(base_dir=self.base_dir):
                    system_uuid = record["system_uuid"]
                    peer_info = self._peers.get(system_uuid)
                    if peer_info is not None and peer_info.get("route") not in _UNCONFIRMED_ROUTES:
                        self._retry_state.pop(system_uuid, None)
                        continue
                    if not (record.get("known_addresses") or []):
                        continue
                    state = self._retry_state.setdefault(system_uuid, {"attempts": 0, "next_attempt": 0.0})
                    if state["attempts"] >= RETRY_MAX_ATTEMPTS or now < state["next_attempt"]:
                        continue
                    try:
                        connection = await self.connect_to_trusted(system_uuid, port=self._listen_port or DEFAULT_LISTEN_PORT)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        state["attempts"] += 1
                        delay = min(RETRY_BASE_SECONDS * (2 ** state["attempts"]), RETRY_MAX_SECONDS)
                        state["next_attempt"] = now + delay
                        state["last_error"] = str(exc)
                        self._emit(
                            {
                                "event": "retry_scheduled",
                                "system_uuid": system_uuid,
                                "attempts": state["attempts"],
                                "next_attempt": state["next_attempt"],
                                "error": str(exc),
                            }
                        )
                        continue
                    await connection.close()
                    self._retry_state.pop(system_uuid, None)
        except asyncio.CancelledError:
            raise

    async def _global_discovery_loop(self, server_url: str) -> None:
        """Real, gated global-discovery announce/reannounce + trusted-device
        query loop (2026-08-08, Codex sync review, "Invoke gated global
        discovery from SyncService, honor configured server URL,
        announce/reannounce timing and query trusted devices, update real
        candidate addresses, and stop all HTTPS work on disable"). Only ever
        runs while cfg["global_discovery"] AND a real server_url were both
        supplied to start() -- see start()'s own gate. Every blocking HTTPS
        call runs in a thread executor so it never blocks the event loop;
        cancelled cleanly by stop(), which stops all further HTTPS work
        immediately (no request is ever in flight after stop() returns,
        since cancellation happens at the next await point between calls)."""
        from pythia.core.sync import global_discovery

        loop = asyncio.get_event_loop()
        reannounce_after = 600.0
        try:
            while True:
                try:
                    # Real local IPv4 + IPv6 candidates plus the real NAT-
                    # mapped external one if active, already deduplicated,
                    # ranked, and stripped of any zone-scoped IPv6 literal
                    # that would be meaningless to a global-discovery peer
                    # (2026-08-08 NAT task; extended 2026-08-09, "internal
                    # IPv6 direct connectivity and dual-stack route
                    # selection") -- same computation _own_relay_candidates()
                    # already does, reused here rather than duplicated so the
                    # two publication paths can never honestly drift apart.
                    # Only announce if we're actually listening -- advertising
                    # an address with no real listener behind it would be
                    # dishonest, not just unhelpful.
                    real_addresses = self._own_relay_candidates()
                    if real_addresses:
                        header = await loop.run_in_executor(
                            self._global_discovery_executor, global_discovery.announce, server_url, self.node, real_addresses
                        )
                        if header:
                            reannounce_after = float(header)
                        self._emit({"event": "global_discovery_announced", "addresses": real_addresses})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._emit({"event": "global_discovery_error", "error": str(exc)})

                for record in trust.list_trusted(base_dir=self.base_dir):
                    system_uuid = record["system_uuid"]
                    if self._peers.get(system_uuid, {}).get("route") not in _UNCONFIRMED_ROUTES:
                        continue
                    try:
                        candidate_addresses = await loop.run_in_executor(
                            self._global_discovery_executor, global_discovery.query_device, server_url, record["device_id"]
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                    if candidate_addresses:
                        trust.update_known_addresses(system_uuid, candidate_addresses, base_dir=self.base_dir)
                        self._record_peer(system_uuid, route="unreachable", addresses=candidate_addresses)

                await asyncio.sleep(max(reannounce_after, GLOBAL_DISCOVERY_MIN_INTERVAL))
        except asyncio.CancelledError:
            raise
