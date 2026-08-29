"""Contract tests for the Slack response_url capability store.

Every URL here is the reserved fake below. A real response_url is a bearer capability: anyone
holding it can post into that conversation as the app, without a token. It must never be committed
to a repository, and a test fixture is a repository file like any other.
"""

import copy
import io
import logging
import pickle
import threading
import urllib.error
import traceback
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from marketing_knowledge_agent.slack_response_urls import (
    MAX_USES,
    ResponseCapability,
    SlackResponseUrlError,
    ResponseReservation,
    SlackResponseUrlStore,
    is_valid_response_url,
    send_response_url_message,
    single_use_reservation,
)


FAKE_URL = "https://hooks.slack.com/commands/TEST/SECRET_CAPABILITY"
CAPABILITY_SECRET = "SECRET_CAPABILITY"
OTHER_FAKE_URL = "https://hooks.slack.com/actions/TEST/SECOND_CAPABILITY"
CTX = {"owner_user_id": "U1", "channel_id": "C1", "session_key": "U1:sess"}
CLICK = {"user_id": "U1", "channel_id": "C1", "session_key": "U1:sess"}


# --------------------------------------------------------------------------------------
# URL validation: this value arrives in a payload and is then POSTed to
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com/commands/T/1/x",
        "https://hooks.slack.com/actions/T/1/x",
        "https://HOOKS.SLACK.COM/commands/T/1/x",   # host comparison is case-insensitive
    ],
)
def test_slack_response_urls_are_accepted(url):
    assert is_valid_response_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/commands/T/1/x",       # plaintext
        "https://evil.test/commands/T/1/x",          # wrong host entirely
        "https://hooks.slack.com.evil.test/commands/T/1/x",       # suffix that only looks like the real host
        "https://hooks.slack.com@evil.test/commands/T/1/x",       # userinfo pointing somewhere else
        "https://evil.test/?u=https://hooks.slack.com/x",
        "ftp://hooks.slack.com/x",
        "//hooks.slack.com/x",
        "", "   ", None, 42, b"https://hooks.slack.com/commands/T/1/x",
    ],
)
def test_everything_else_is_refused(url):
    """Accepting an arbitrary host would make this a request-forgery primitive.

    The value is taken from a payload and then POSTed to, so the check is an exact host allowlist
    rather than a substring or a pattern -- ``hooks.slack.com.evil.test`` and
    ``hooks.slack.com@evil.test`` both contain the real host as a substring.
    """
    assert is_valid_response_url(url) is False


def test_an_invalid_url_is_never_stored():
    store = SlackResponseUrlStore()
    assert store.store("https://evil.test/commands/T/1/x", **CTX) is False
    assert len(store) == 0
    assert store.remaining_uses(**CLICK) == 0


# --------------------------------------------------------------------------------------
# secrecy
# --------------------------------------------------------------------------------------


def test_the_capability_is_absent_from_repr():
    """A debugger, a crash dump or a stray print must not be able to disclose it."""
    capability = ResponseCapability(
        url=FAKE_URL, owner_user_id="U1", channel_id="C1",
        session_key="U1:sess", expires_at=1e9, remaining_uses=5,
    )
    assert "SECRET_CAPABILITY" not in repr(capability)
    assert "U1" in repr(capability)  # the non-secret context still shows, for diagnostics


def test_the_store_never_discloses_the_url_by_listing():
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)
    assert "SECRET_CAPABILITY" not in repr(store)
    assert "SECRET_CAPABILITY" not in str(len(store))


# --------------------------------------------------------------------------------------
# ownership: unknown, wrong and expired are one outcome
# --------------------------------------------------------------------------------------


def test_the_owner_can_take_the_capability():
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)
    assert store.reserve(**CLICK).spend() == FAKE_URL


@pytest.mark.parametrize(
    "wrong",
    [
        {"user_id": "U2", "channel_id": "C1", "session_key": "U1:sess"},
        {"user_id": "U1", "channel_id": "C_OTHER", "session_key": "U1:sess"},
        {"user_id": "U1", "channel_id": "C1", "session_key": "other:sess"},
        {"user_id": "", "channel_id": "C1", "session_key": "U1:sess"},
    ],
    ids=["wrong_user", "wrong_channel", "wrong_session", "empty_user"],
)
def test_any_context_mismatch_resolves_to_nothing(wrong):
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)

    assert store.reserve(**wrong) is None
    assert store.remaining_uses(**wrong) == 0
    # And it did not spend the owner's budget on the way.
    assert store.remaining_uses(**CLICK) == MAX_USES


def test_an_unknown_context_is_indistinguishable_from_a_wrong_one():
    store = SlackResponseUrlStore()
    assert store.reserve(**CLICK) is None
    store.store(FAKE_URL, **CTX)
    assert store.reserve(user_id="U2", channel_id="C1", session_key="U1:sess") is None


def test_incomplete_context_is_refused_at_store_time():
    """An empty stored value would compare equal to an empty derived one."""
    store = SlackResponseUrlStore()
    for missing in ("owner_user_id", "channel_id", "session_key"):
        ctx = dict(CTX, **{missing: "  "})
        assert store.store(FAKE_URL, **ctx) is False
    assert len(store) == 0


# --------------------------------------------------------------------------------------
# platform-imposed bounds
# --------------------------------------------------------------------------------------


def test_the_use_budget_matches_slacks_and_is_enforced_locally():
    """Slack allows five sends per response_url; the sixth fails here, not there."""
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)

    for _ in range(MAX_USES):
        assert store.reserve(**CLICK).spend() == FAKE_URL

    assert store.reserve(**CLICK) is None
    assert store.remaining_uses(**CLICK) == 0


def test_the_ttl_sits_below_slacks_documented_lifetime():
    from marketing_knowledge_agent.slack_response_urls import DEFAULT_TTL_SECONDS

    assert 0 < DEFAULT_TTL_SECONDS < 30 * 60


def test_an_expired_capability_refuses_locally():
    clock = [1000.0]
    store = SlackResponseUrlStore(ttl_seconds=60, clock=lambda: clock[0])
    store.store(FAKE_URL, **CTX)

    assert store.remaining_uses(**CLICK) > 0
    clock[0] += 61
    assert store.remaining_uses(**CLICK) == 0
    assert store.reserve(**CLICK) is None


# --------------------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------------------


def test_storing_again_replaces_the_capability_and_restores_the_budget():
    """A button click carries a newer capability than the command that began the session."""
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)
    store.reserve(**CLICK)
    assert store.remaining_uses(**CLICK) == MAX_USES - 1

    assert store.store(OTHER_FAKE_URL, **CTX) is True

    assert store.remaining_uses(**CLICK) == MAX_USES
    assert store.reserve(**CLICK).spend() == OTHER_FAKE_URL


def test_a_refresh_with_an_invalid_url_leaves_the_existing_capability_alone():
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)

    assert store.store("https://evil.test/commands/T/1/x", **CTX) is False

    assert store.reserve(**CLICK).spend() == FAKE_URL


def test_capabilities_are_bounded_by_entry_count():
    store = SlackResponseUrlStore(max_entries=2)
    for i in range(4):
        store.store(FAKE_URL, owner_user_id=f"U{i}", channel_id="C1", session_key=f"U{i}:s")
    assert len(store) == 2


# ======================================================================================
# Independent review R1 — the four blocking findings
# ======================================================================================

# --------------------------------------------------------------------------------------
# Finding 1 / 18: atomic use accounting under real concurrency
# --------------------------------------------------------------------------------------


def _race(store, threads, target):
    """Run ``target`` on N threads released simultaneously, and collect what each returned.

    A barrier rather than a loop-and-hope: the previous implementation's window sat between the
    liveness check and the decrement, so a probabilistic test could run for a long time without
    ever landing in it. Forcing the overlap makes the result deterministic either way.
    """
    barrier = threading.Barrier(threads)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait(timeout=5)
        outcome = target()
        with lock:
            results.append(outcome)

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=10)
    return results


def test_two_threads_racing_for_the_final_use_produce_exactly_one_send():
    """Finding 1, the blocking one. Reproduced against the previous candidate as 2 successes."""
    store = SlackResponseUrlStore(max_uses=1)
    store.store(FAKE_URL, **CTX)

    results = _race(store, 2, lambda: store.reserve(**CLICK))

    assert sum(1 for r in results if r is not None) == 1
    assert sum(1 for r in results if r is None) == 1
    assert store.remaining_uses(**CLICK) == 0


def test_the_race_is_forced_rather_than_hoped_for():
    """The same race with the check-then-act window held open explicitly.

    Both threads are made to complete the liveness check before either may decrement -- which is
    exactly the interleaving that produced a double-spend before the lock existed. If the critical
    section were removed, this is the test that would catch it deterministically.
    """
    store = SlackResponseUrlStore(max_uses=1)
    store.store(FAKE_URL, **CTX)

    checked = threading.Barrier(2, timeout=5)
    original_is_live = ResponseCapability.is_live

    def blocking_is_live(self, now):
        result = original_is_live(self, now)
        try:
            checked.wait()
        except threading.BrokenBarrierError:
            # Only one thread reached the check, which means the lock serialised them -- the
            # outcome this test wants.
            pass
        return result

    ResponseCapability.is_live = blocking_is_live
    try:
        results = _race(store, 2, lambda: store.reserve(**CLICK))
    finally:
        ResponseCapability.is_live = original_is_live

    assert sum(1 for r in results if r is not None) == 1


def test_two_threads_and_two_uses_produce_exactly_two_sends():
    """Matrix B: the lock must not over-serialise into losing a legitimate use."""
    store = SlackResponseUrlStore(max_uses=2)
    store.store(FAKE_URL, **CTX)

    results = _race(store, 2, lambda: store.reserve(**CLICK))

    assert sum(1 for r in results if r is not None) == 2
    assert store.remaining_uses(**CLICK) == 0


def test_no_thread_wins_against_an_expired_capability():
    """Matrix C."""
    clock = [1000.0]
    store = SlackResponseUrlStore(ttl_seconds=60, max_uses=5, clock=lambda: clock[0])
    store.store(FAKE_URL, **CTX)
    clock[0] += 61

    results = _race(store, 4, lambda: store.reserve(**CLICK))

    assert all(r is None for r in results)


def test_refresh_racing_reserve_leaves_a_coherent_state():
    """Matrix D: no negative budget, no torn record, no duplicate use of one capability."""
    store = SlackResponseUrlStore(max_uses=1)
    store.store(FAKE_URL, **CTX)

    def action():
        if threading.current_thread().name.endswith("-0"):
            return ("refresh", store.store(OTHER_FAKE_URL, **CTX))
        return ("reserve", store.reserve(**CLICK))

    for _ in range(50):
        store.store(FAKE_URL, **CTX)
        results = _race(store, 2, action)
        reserved = [r for kind, r in results if kind == "reserve" and r is not None]
        assert len(reserved) <= 1
        assert store.remaining_uses(**CLICK) >= 0


def test_discard_racing_reserve_never_corrupts_the_store():
    """Matrix E: at most one valid outcome, and no exception escapes either thread."""
    store = SlackResponseUrlStore(max_uses=1)

    def action():
        if threading.current_thread().name.endswith("-0"):
            store.discard(**CLICK)
            return None
        return store.reserve(**CLICK)

    for _ in range(50):
        store.store(FAKE_URL, **CTX)
        results = _race(store, 2, action)
        assert sum(1 for r in results if r is not None) <= 1
        assert len(store) >= 0


def test_a_wrong_context_never_decrements_the_legitimate_record():
    """Matrix F."""
    store = SlackResponseUrlStore(max_uses=2)
    store.store(FAKE_URL, **CTX)

    for wrong in (
        {"user_id": "U2", "channel_id": "C1", "session_key": "U1:sess"},
        {"user_id": "U1", "channel_id": "C_OTHER", "session_key": "U1:sess"},
        {"user_id": "U1", "channel_id": "C1", "session_key": "nope"},
    ):
        assert store.reserve(**wrong) is None

    assert store.remaining_uses(**CLICK) == 2


def test_the_store_serialises_its_mutable_state():
    """The lock is the mechanism; its absence is what the probes remove."""
    store = SlackResponseUrlStore()
    assert isinstance(store._lock, type(threading.RLock()))


# --------------------------------------------------------------------------------------
# Finding 1 / 4: reservations are one-shot
# --------------------------------------------------------------------------------------


def test_a_reservation_can_be_spent_exactly_once():
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)
    reservation = store.reserve(**CLICK)

    assert reservation.spend() == FAKE_URL
    assert reservation.spent is True
    with pytest.raises(SlackResponseUrlError):
        reservation.spend()


def test_a_reservation_hides_its_url_from_repr():
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)

    assert CAPABILITY_SECRET not in repr(store.reserve(**CLICK))


def test_a_single_use_reservation_validates_before_issuing():
    """The denial and stale-guidance paths answer without a stored session."""
    assert single_use_reservation("https://evil.test/commands/X") is None
    assert single_use_reservation("").__class__ is type(None)
    reservation = single_use_reservation(FAKE_URL)
    assert reservation.spend() == FAKE_URL


def test_a_refresh_does_not_revoke_a_reservation_already_issued():
    """Its use was consumed atomically when it was issued, so it is a send already paid for.

    Revoking it would drop a reply the user is owed rather than prevent one they are not.
    """
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)
    outstanding = store.reserve(**CLICK)

    store.store(OTHER_FAKE_URL, **CTX)

    assert outstanding.spend() == FAKE_URL


# --------------------------------------------------------------------------------------
# Finding 2 / 20: strict URL validation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # Every one of these was accepted by the previous, looser check.
        "https://user:pass@hooks.slack.com/commands/TEST/X",
        "https://user@hooks.slack.com/commands/TEST/X",
        "https://:pass@hooks.slack.com/commands/TEST/X",
        "https://hooks.slack.com:444/commands/TEST/X",
        "https://hooks.slack.com:8443/commands/TEST/X",
        "https://hooks.slack.com:notaport/commands/TEST/X",
        "https://hooks.slack.com.evil.test/commands/TEST/X",
        "https://evilhooks.slack.com/commands/TEST/X",
        "https://hooks.slack.com@evil.test/commands/TEST/X",
        "https://hooks.slack.com/commands/TEST/X#fragment",
        "https://hooks.slack.com/not-a-response-url/X",
        "https://127.0.0.1/commands/TEST/X",
        "https://[::1]/commands/TEST/X",
        "https:///commands/TEST/X",
    ],
)
def test_the_hostile_url_shapes_are_all_refused(url):
    """Each clause exists because the looser check let this exact shape through.

    The value arrives in a payload and is then POSTed to, so anything accepted here is a request
    this app makes on someone else's behalf.
    """
    assert is_valid_response_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com/commands/TEST/X",
        "https://hooks.slack.com:443/commands/TEST/X",
        "https://hooks.slack.com/actions/TEST/X",
        "https://HOOKS.SLACK.COM/commands/TEST/X",
    ],
)
def test_the_approved_shapes_are_accepted(url):
    assert is_valid_response_url(url) is True


# --------------------------------------------------------------------------------------
# Finding 2 / 10 / 21: the transport refuses redirects
# --------------------------------------------------------------------------------------


class _RecordingHandler(BaseHTTPRequestHandler):
    """Answers the first request with a redirect and records everything it receives."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - a followed 302/303 arrives here as a GET
        self.server.requests.append(self.path)
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's own naming
        self.server.requests.append(self.path)
        behaviour = self.server.behaviour
        if behaviour == "redirect":
            self.send_response(self.server.redirect_code)
            self.send_header("Location", self.server.redirect_target)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif behaviour == "hang":
            time.sleep(3)          # longer than the transport's timeout
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif behaviour in ("server_error", "client_error"):
            self.send_response(500 if behaviour == "server_error" else 400)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        return


@contextmanager
def _local_server(behaviour, redirect_target="", redirect_code=307):
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    server.requests = []
    server.behaviour = behaviour
    server.redirect_target = redirect_target
    server.redirect_code = redirect_code
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _reservation_for(server):
    """A reservation pointing at the local server, bypassing host validation deliberately.

    Validation is tested separately and exhaustively above. What is under test here is the
    *transport*: given a URL it has been told to use, how does it behave when the far end
    misbehaves? Constructing the reservation directly is the only way to exercise that without
    contacting Slack.
    """
    host, port = server.server_address
    return ResponseReservation(f"http://{host}:{port}/commands/TEST/SECRET_CAPABILITY")


@pytest.mark.parametrize("code", [301, 302, 303, 307])
def test_a_redirect_is_refused_and_the_target_is_never_contacted(code):
    """Finding 2, redirect half. Validating the first hop authorizes nothing about the second.

    Two things make this test mean something.

    The capture target is a **real second server**, not a dead port: pointing a redirect at a
    closed port makes a followed redirect fail too, so the send would raise either way and the
    assertion would hold whether or not redirects were being chased. A live listener is the only
    thing that can tell "refused" from "followed and failed".

    And every status is covered, not just 307. ``urllib``'s own ``HTTPRedirectHandler`` already
    refuses 307 on a POST, so 307 alone would pass with the guard removed -- a mutation probe
    proved exactly that. 301/302/303 are the ones the standard library *does* follow (converting
    the POST to a GET), and those are where the guard actually bears.
    """
    with _local_server("ok") as capture:
        capture_host, capture_port = capture.server_address
        capture_url = f"http://{capture_host}:{capture_port}/capture"

        with _local_server("redirect", redirect_target=capture_url, redirect_code=code) as origin:
            with pytest.raises(SlackResponseUrlError):
                send_response_url_message(_reservation_for(origin), {"text": "hello"})

            # Exactly one outbound attempt, and it went nowhere near the redirect target.
            assert len(origin.requests) == 1
            assert capture.requests == []


def test_the_capture_server_would_notice_a_followed_redirect():
    """Proves the assertion above is not vacuous.

    If the transport did follow redirects, the capture server would record the second request. This
    test drives that server directly, so a future change that made ``capture.requests`` unreachable
    for an unrelated reason would fail here rather than silently weakening the redirect test.
    """
    with _local_server("ok") as capture:
        host, port = capture.server_address
        send_response_url_message(
            ResponseReservation(f"http://{host}:{port}/capture"), {"text": "hello"}
        )

        assert capture.requests == ["/capture"]


def test_a_successful_send_makes_exactly_one_request():
    with _local_server("ok") as origin:
        send_response_url_message(_reservation_for(origin), {"text": "hello"})

        assert len(origin.requests) == 1


def test_a_server_error_is_not_retried():
    """One reservation is one attempt. The SDK client was dropped partly for retrying by default.

    Retrying would turn one locally-accounted use into several requests against Slack's own
    five-use budget.
    """
    with _local_server("server_error") as origin:
        with pytest.raises(SlackResponseUrlError):
            send_response_url_message(_reservation_for(origin), {"text": "hello"})

        assert len(origin.requests) == 1


# --------------------------------------------------------------------------------------
# Finding 3 / 22: the capability never reaches a log or an exception
# --------------------------------------------------------------------------------------


@contextmanager
def _capture_all_logs():
    """Everything any logger emits, at every level, while the block runs."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@pytest.mark.parametrize("behaviour", ["ok", "redirect", "server_error"])
def test_no_log_line_anywhere_contains_the_capability(behaviour):
    """Finding 3. Captured at DEBUG, so a lower app level cannot be what is hiding it."""
    with _local_server(
        behaviour, redirect_target="http://127.0.0.1:9/capture", redirect_code=302
    ) as origin:
        reservation = _reservation_for(origin)
        with _capture_all_logs() as logs:
            try:
                send_response_url_message(reservation, {"text": "hello"})
            except SlackResponseUrlError:
                pass

        captured = logs.getvalue()
        assert CAPABILITY_SECRET not in captured
        assert "/commands/TEST/" not in captured


@pytest.mark.parametrize("behaviour", ["redirect", "server_error"])
def test_the_exception_a_caller_sees_carries_no_capability(behaviour):
    """It reaches bolt's error handler and the logs, so it must be safe to print."""
    with _local_server(
        behaviour, redirect_target="http://127.0.0.1:9/capture", redirect_code=302
    ) as origin:
        with pytest.raises(SlackResponseUrlError) as exc_info:
            send_response_url_message(_reservation_for(origin), {"text": "hello"})

    rendered = f"{exc_info.value!r} {exc_info.value} {traceback.format_exc()}"
    assert CAPABILITY_SECRET not in rendered
    assert "/commands/TEST/" not in rendered
    # The chained original is suppressed, because urllib's own error carries the full URL.
    assert exc_info.value.__cause__ is None


def test_a_transport_failure_carries_no_capability_either():
    """Nothing is listening on this port, so urllib raises before any HTTP happens."""
    reservation = ResponseReservation("http://127.0.0.1:9/commands/TEST/SECRET_CAPABILITY")
    with _capture_all_logs() as logs:
        with pytest.raises(SlackResponseUrlError) as exc_info:
            send_response_url_message(reservation, {"text": "hello"})

    assert CAPABILITY_SECRET not in str(exc_info.value)
    assert CAPABILITY_SECRET not in logs.getvalue()


def test_a_stored_capability_hides_its_url_from_repr():
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)

    assert CAPABILITY_SECRET not in repr(store)
    assert CAPABILITY_SECRET not in repr(
        ResponseCapability(
            url=FAKE_URL, owner_user_id="U1", channel_id="C1",
            session_key="U1:sess", expires_at=1e9, remaining_uses=5,
        )
    )


def test_a_failed_send_does_not_refund_the_use():
    """Slack may have received the request even when the client saw an error.

    Refunding could exceed the server-side five-use budget and deliver the same message twice.
    """
    store = SlackResponseUrlStore(max_uses=1)
    store.store(FAKE_URL, **CTX)
    reservation = store.reserve(**CLICK)
    assert store.remaining_uses(**CLICK) == 0

    with _local_server("server_error") as origin:
        failing = ResponseReservation(_reservation_for(origin)._url)
        with pytest.raises(SlackResponseUrlError):
            send_response_url_message(failing, {"text": "hello"})

    # The store is untouched by a transport failure; the use stays spent.
    assert store.remaining_uses(**CLICK) == 0
    assert reservation.spent is False


# ======================================================================================
# Independent Security Review R2 — four blocking findings
# ======================================================================================

# --------------------------------------------------------------------------------------
# R2 finding 1: one reservation, one send, even under concurrency
# --------------------------------------------------------------------------------------


def test_one_reservation_concurrently_spent_by_two_threads_sends_once():
    """The store's lock protects the budget; this protects the single authorization it bought.

    Reproduced against the previous candidate as 2 outbound requests from the same reservation
    object. Forced overlap rather than a probabilistic loop -- the window is a check-then-act, so a
    loop can run for a long time without landing in it.
    """
    reservation = ResponseReservation(FAKE_URL)
    barrier = threading.Barrier(2, timeout=5)
    won, refused = [], []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            url = reservation.spend()
        except SlackResponseUrlError:
            with lock:
                refused.append(1)
        else:
            with lock:
                won.append(url)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)

    assert len(won) == 1
    assert len(refused) == 1


def test_two_concurrent_sends_of_one_reservation_make_one_http_request():
    """The same guarantee where it actually matters: outbound requests, not local calls."""
    with _local_server("ok") as origin:
        reservation = _reservation_for(origin)
        barrier = threading.Barrier(2, timeout=5)
        errors = []

        def worker():
            barrier.wait()
            try:
                send_response_url_message(reservation, {"text": "hello"})
            except SlackResponseUrlError:
                errors.append(1)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

        assert len(origin.requests) == 1
        assert len(errors) == 1


def test_the_reservation_owns_its_own_lock():
    """The mechanism the probe removes."""
    assert isinstance(ResponseReservation(FAKE_URL)._lock, type(threading.Lock()))


# --------------------------------------------------------------------------------------
# R2 finding 2: a reservation cannot be duplicated into a second authorization
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "duplicate"),
    [
        ("copy", copy.copy),
        ("deepcopy", copy.deepcopy),
        ("pickle", pickle.dumps),
    ],
)
@pytest.mark.parametrize("already_spent", [False, True])
def test_a_reservation_cannot_be_duplicated(label, duplicate, already_spent):
    """Each of these minted a second send from one authorization.

    ``copy``/``deepcopy`` produced a clone with the same bearer URL and a fresh unspent flag;
    ``pickle`` additionally wrote the capability into bytes that outlive the process. Refused for a
    spent reservation as well as an unspent one -- a duplicate of a spent one is still a copy of
    the secret.
    """
    reservation = ResponseReservation(FAKE_URL)
    if already_spent:
        reservation.spend()

    with pytest.raises(TypeError) as exc_info:
        duplicate(reservation)

    # Asserting *our* refusal, not merely that something raised. ``deepcopy`` and ``pickle`` would
    # also fail incidentally because the owned ``Lock`` cannot be copied -- so a test that accepted
    # any ``TypeError`` would keep passing if the explicit guards were deleted and the lock were
    # later replaced with something copyable.
    assert "response capability reservation" in str(exc_info.value)
    # The refusal itself must not disclose what it is protecting.
    assert CAPABILITY_SECRET not in str(exc_info.value)


def test_a_reservation_carries_no_instance_dictionary_to_copy():
    """``__slots__`` is why the duplication protocols have nothing to work with by default."""
    assert not hasattr(ResponseReservation(FAKE_URL), "__dict__")


def test_the_reservation_repr_shows_state_but_never_the_capability():
    unspent = ResponseReservation(FAKE_URL)
    spent = ResponseReservation(FAKE_URL)
    spent.spend()

    assert CAPABILITY_SECRET not in repr(unspent)
    assert CAPABILITY_SECRET not in repr(spent)
    assert "spent=False" in repr(unspent) and "spent=True" in repr(spent)


# --------------------------------------------------------------------------------------
# R2 finding 3: the sanitized error carries no secret anywhere in its tree
# --------------------------------------------------------------------------------------


def _exception_tree_strings(exc, depth=0, seen=None):
    """Everything a structured error reporter could serialize from an exception.

    Deliberately not a formatted traceback: ``raise ... from None`` suppresses *rendering* while
    leaving the original reachable through ``__context__``, which is exactly how the capability
    survived the previous sanitization. A reporter that walks objects finds what a printed
    traceback hides.
    """
    seen = seen if seen is not None else set()
    if exc is None or id(exc) in seen or depth > 6:
        return []
    seen.add(id(exc))

    parts = [type(exc).__name__]
    for render in (str, repr):
        try:
            parts.append(render(exc))
        except Exception:  # noqa: BLE001 - a reporter would swallow this too
            pass
    try:
        parts.append(repr(getattr(exc, "args", None)))
    except Exception:  # noqa: BLE001
        pass
    for attribute in ("url", "full_url", "reason", "request", "response", "filename", "hdrs"):
        if hasattr(exc, attribute):
            try:
                parts.append(f"{attribute}={getattr(exc, attribute)!r}")
            except Exception:  # noqa: BLE001 - a reporter would swallow this too
                pass
    traceback_frame = getattr(exc, "__traceback__", None)
    while traceback_frame is not None:
        try:
            parts.append(repr(traceback_frame.tb_frame.f_locals))
        except Exception:  # noqa: BLE001
            pass
        traceback_frame = traceback_frame.tb_next

    parts += _exception_tree_strings(getattr(exc, "__cause__", None), depth + 1, seen)
    parts += _exception_tree_strings(getattr(exc, "__context__", None), depth + 1, seen)
    return parts


@pytest.mark.parametrize(
    ("label", "behaviour", "code"),
    [
        ("http_400", "client_error", None),
        ("http_500", "server_error", None),
        ("redirect_302", "redirect", 302),
        ("redirect_307", "redirect", 307),
    ],
)
def test_no_capability_survives_anywhere_in_the_exception_tree(label, behaviour, code):
    """R2 finding 3. ``__context__`` held the original ``HTTPError``, whose ``.url`` is the secret."""
    with _local_server(
        behaviour, redirect_target="http://127.0.0.1:9/capture", redirect_code=code or 307
    ) as origin:
        with pytest.raises(SlackResponseUrlError) as exc_info:
            send_response_url_message(_reservation_for(origin), {"text": "hello"})

    error = exc_info.value
    assert error.__cause__ is None
    assert error.__context__ is None
    tree = " || ".join(_exception_tree_strings(error))
    assert CAPABILITY_SECRET not in tree
    assert "/commands/TEST/" not in tree


def test_no_capability_survives_a_connection_failure_either():
    """Nothing is listening, so ``urllib`` raises a ``URLError`` before any HTTP happens."""
    reservation = ResponseReservation("http://127.0.0.1:9/commands/TEST/SECRET_CAPABILITY")

    with pytest.raises(SlackResponseUrlError) as exc_info:
        send_response_url_message(reservation, {"text": "hello"})

    error = exc_info.value
    assert error.__cause__ is None and error.__context__ is None
    assert CAPABILITY_SECRET not in " || ".join(_exception_tree_strings(error))


def test_the_reporter_walker_would_notice_a_leak():
    """Proves the assertions above are not vacuous.

    A deliberately unsanitized error -- the shape the previous candidate produced -- is walked by
    the same function, which must find the secret. Without this, a walker that silently returned
    nothing would make every test above pass.
    """
    try:
        try:
            raise urllib.error.HTTPError(FAKE_URL, 500, "boom", {}, io.BytesIO(b""))
        except urllib.error.HTTPError:
            raise SlackResponseUrlError("sanitized") from None
    except SlackResponseUrlError as leaky:
        assert leaky.__context__ is not None
        assert CAPABILITY_SECRET in " || ".join(_exception_tree_strings(leaky))


# --------------------------------------------------------------------------------------
# R2 finding 4: path validation is structural, not a prefix test
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # dot segments, raw and encoded, in both cases
        "https://hooks.slack.com/commands/a/../x",
        "https://hooks.slack.com/commands/a/../../actions/x",
        "https://hooks.slack.com/commands/../admin",
        "https://hooks.slack.com/commands/%2e%2e/x",
        "https://hooks.slack.com/commands/%2E%2E/x",
        "https://hooks.slack.com/commands/%2e./x",
        "https://hooks.slack.com/commands/.%2e/x",
        "https://hooks.slack.com/commands/./x",
        # encoded separators that would redraw segment boundaries after decoding
        "https://hooks.slack.com/commands/a%2f..%2factions/x",
        "https://hooks.slack.com/commands/a%2F..%2Factions/x",
        "https://hooks.slack.com/commands/a%5c..%5cx",
        "https://hooks.slack.com/commands/a%5C..%5Cx",
        "https://hooks.slack.com/commands/a\\..\\x",
        # malformed and control-character escapes
        "https://hooks.slack.com/commands/%zz",
        "https://hooks.slack.com/commands/%2",
        "https://hooks.slack.com/commands/%",
        "https://hooks.slack.com/commands/%00x",
        "https://hooks.slack.com/commands/%0ax",
        "https://hooks.slack.com/commands/%7f",
        # empty segments and shapes with no payload segment
        "https://hooks.slack.com/commands//x",
        "https://hooks.slack.com/commands/",
        "https://hooks.slack.com/commands",
        "https://hooks.slack.com/",
        # endpoint families this app never receives
        "https://hooks.slack.com/services/TEST/X",
        "https://hooks.slack.com/workflows/TEST/X",
        "https://hooks.slack.com/api/chat.postMessage",
        # query strings
        "https://hooks.slack.com/commands/TEST/X?a=1",
    ],
)
def test_path_bypasses_are_refused(url):
    """R2 finding 4. A prefix test authorized these on the characters they start with."""
    assert is_valid_response_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com/commands/T0001/1234/abcd",
        "https://hooks.slack.com/actions/T0001/1234/abcd",
        "https://hooks.slack.com:443/commands/T0001/1234/abcd",
    ],
)
def test_the_two_families_this_surface_actually_receives_are_accepted(url):
    """``commands`` from a slash payload, ``actions`` from an interactive one, and nothing else."""
    assert is_valid_response_url(url) is True


def test_the_approved_families_are_exactly_the_two_entry_points():
    from marketing_knowledge_agent.slack_response_urls import (
        ALLOWED_RESPONSE_URL_PATH_FAMILIES,
    )

    assert ALLOWED_RESPONSE_URL_PATH_FAMILIES == frozenset({"commands", "actions"})


# ======================================================================================
# Independent Security Review R3 — traceback secrecy and lock regression strength
# ======================================================================================


def _find_secret(obj, depth=0, seen=None):
    """Reviewer-grade recursive detector: walks objects, never their ``repr``.

    The previous version of these tests compared ``repr(f_locals)``, which this module's own
    ``__repr__`` masks -- so a spent reservation sitting in a caller frame, still holding its URL,
    went undetected. This walks ``__slots__``, ``__dict__``, containers and the URL-bearing
    attributes ``urllib`` uses, with cycle protection.
    """
    seen = seen if seen is not None else set()
    if depth > 8 or id(obj) in seen:
        return False
    seen.add(id(obj))

    if isinstance(obj, str):
        return CAPABILITY_SECRET in obj
    if isinstance(obj, (bytes, bytearray)):
        return CAPABILITY_SECRET.encode() in obj
    if isinstance(obj, dict):
        return any(
            _find_secret(k, depth + 1, seen) or _find_secret(v, depth + 1, seen)
            for k, v in list(obj.items())
        )
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(_find_secret(item, depth + 1, seen) for item in list(obj))

    for slot in getattr(type(obj), "__slots__", ()) or ():
        try:
            if _find_secret(getattr(obj, slot), depth + 1, seen):
                return True
        except Exception:  # noqa: BLE001 - a reporter would swallow this too
            pass
    for attribute in ("url", "full_url", "request", "reason", "args", "filename"):
        if hasattr(obj, attribute):
            try:
                if _find_secret(getattr(obj, attribute), depth + 1, seen):
                    return True
            except Exception:  # noqa: BLE001
                pass
    instance_dict = getattr(obj, "__dict__", None)
    if isinstance(instance_dict, dict) and _find_secret(dict(instance_dict), depth + 1, seen):
        return True
    return False


def _leaking_frames(exc, depth=0, seen=None):
    """Which frames in an exception's whole tree expose the capability.

    Frames belonging to this test module are skipped: a test's own fixtures legitimately hold the
    sentinel, and counting them would make every result a false positive. Everything under
    ``marketing_knowledge_agent`` -- and any other caller -- is fair game.
    """
    seen = seen if seen is not None else set()
    if exc is None or id(exc) in seen or depth > 6:
        return []
    seen.add(id(exc))

    leaks = []
    if _find_secret(getattr(exc, "args", ())):
        leaks.append(f"{type(exc).__name__}.args")

    traceback_frame = exc.__traceback__
    while traceback_frame is not None:
        frame = traceback_frame.tb_frame
        if frame.f_globals.get("__name__") != __name__:
            if _find_secret(dict(frame.f_locals)):
                leaks.append(f"{frame.f_code.co_filename.rsplit('/', 1)[-1]}:{frame.f_code.co_name}")
        traceback_frame = traceback_frame.tb_next

    leaks += _leaking_frames(getattr(exc, "__cause__", None), depth + 1, seen)
    leaks += _leaking_frames(getattr(exc, "__context__", None), depth + 1, seen)
    return leaks


def _send_through_a_holding_caller(reservation, payload):
    """A caller frame that keeps the reservation, exactly as the real handlers do."""
    held = reservation
    send_response_url_message(held, payload)


def test_a_spent_reservation_no_longer_holds_the_capability():
    """R3 finding 1, at its root. The transfer out of the reservation is destructive."""
    reservation = ResponseReservation(FAKE_URL)

    assert reservation.spend() == FAKE_URL

    assert reservation.spent is True
    assert reservation._url is None
    assert _find_secret(reservation) is False


def test_a_reservation_is_emptied_even_when_the_send_fails():
    """A failed send is not refunded, and the capability does not come back either."""
    with _local_server("server_error") as origin:
        reservation = _reservation_for(origin)
        with pytest.raises(SlackResponseUrlError):
            send_response_url_message(reservation, {"text": "hello"})

    assert reservation.spent is True
    assert reservation._url is None


@pytest.mark.parametrize(
    ("label", "behaviour", "code"),
    [
        ("http_400", "client_error", None),
        ("http_500", "server_error", None),
        ("redirect_302", "redirect", 302),
        ("redirect_307", "redirect", 307),
    ],
)
def test_no_traceback_frame_exposes_the_capability(label, behaviour, code):
    """R3 finding 1. ``__cause__``/``__context__`` were already clean; the frames were not."""
    with _local_server(
        behaviour, redirect_target="http://127.0.0.1:9/capture", redirect_code=code or 307
    ) as origin:
        with pytest.raises(SlackResponseUrlError) as exc_info:
            _send_through_a_holding_caller(_reservation_for(origin), {"text": "hello"})

    error = exc_info.value
    assert error.__cause__ is None and error.__context__ is None
    assert _leaking_frames(error) == []


def test_no_traceback_frame_exposes_the_capability_on_a_connection_failure():
    reservation = ResponseReservation("http://127.0.0.1:9/commands/TEST/SECRET_CAPABILITY")

    with pytest.raises(SlackResponseUrlError) as exc_info:
        _send_through_a_holding_caller(reservation, {"text": "hello"})

    assert _leaking_frames(exc_info.value) == []


def test_no_traceback_frame_exposes_the_capability_on_a_timeout():
    """A timeout raises from inside ``urllib``, so its frames are the deepest ones checked."""
    with _local_server("hang") as origin:
        reservation = _reservation_for(origin)
        with pytest.raises(SlackResponseUrlError) as exc_info:
            _send_through_a_holding_caller(reservation, {"text": "hello"})

    assert _leaking_frames(exc_info.value) == []


def test_the_detector_finds_a_capability_that_really_is_reachable():
    """Control case. Without this, a detector that silently found nothing would pass everything.

    The unsafe shape is the previous candidate's: a sanitized error raised from a frame that still
    holds an object carrying the URL.

    It is compiled into a *synthetic module namespace* rather than defined here, because
    ``_leaking_frames`` deliberately skips frames belonging to this test module -- a test's own
    fixtures legitimately hold the sentinel. A control case defined inline would be skipped by that
    same filter and prove nothing, which is exactly what happened on the first attempt.
    """
    namespace = {
        "__name__": "synthetic_not_the_test_module",
        "SlackResponseUrlError": SlackResponseUrlError,
        "FAKE_URL": FAKE_URL,
    }
    exec(  # noqa: S102 - a controlled literal, to obtain a frame outside this module
        "class _LeakyHolder:\n"
        "    def __init__(self, url):\n"
        "        self.url = url\n"
        "\n"
        "def unsafe_caller():\n"
        "    holder = _LeakyHolder(FAKE_URL)\n"
        "    raise SlackResponseUrlError('sanitized')\n",
        namespace,
    )

    with pytest.raises(SlackResponseUrlError) as exc_info:
        namespace["unsafe_caller"]()

    leaks = _leaking_frames(exc_info.value)
    assert leaks != [], "the detector cannot see a capability it should have found"
    assert any("unsafe_caller" in leak for leak in leaks)


# --------------------------------------------------------------------------------------
# R3 finding 3 (P2): the reservation's lock must be provably entered
# --------------------------------------------------------------------------------------


def test_the_spend_transition_actually_enters_the_reservations_lock():
    """R3's non-blocking finding: removing the lock failed nothing.

    The behavioural race test is probabilistic about *when* it would notice, so it kept passing
    with the lock deleted. This is deterministic and white-box: the reservation's own lock is
    swapped for one that records entry, and the state transition must happen inside it.

    Kept alongside the behavioural test rather than replacing it -- one proves the runtime outcome,
    this one proves the guard cannot silently disappear in a refactor.
    """
    reservation = ResponseReservation(FAKE_URL)
    entered = []
    real_lock = reservation._lock

    class _TrackingLock:
        def __enter__(self):
            entered.append("enter")
            return real_lock.__enter__()

        def __exit__(self, *exc_info):
            entered.append("exit")
            return real_lock.__exit__(*exc_info)

    reservation._lock = _TrackingLock()

    assert reservation.spend() == FAKE_URL

    assert entered == ["enter", "exit"], "spend() did not run inside the reservation's lock"


def test_the_spend_transition_holds_the_lock_across_the_whole_transition():
    """Entering is not enough: the check, the mark and the hand-over must all be inside it."""
    reservation = ResponseReservation(FAKE_URL)
    observed = []
    real_lock = reservation._lock

    class _ObservingLock:
        def __enter__(self):
            observed.append(("enter", reservation._spent, reservation._url is not None))
            return real_lock.__enter__()

        def __exit__(self, *exc_info):
            observed.append(("exit", reservation._spent, reservation._url is not None))
            return real_lock.__exit__(*exc_info)

    reservation._lock = _ObservingLock()
    reservation.spend()

    # Unspent-and-holding on the way in; spent-and-empty on the way out. There is no observable
    # spent-with-secret state, which is the property the destructive transfer buys.
    assert observed == [("enter", False, True), ("exit", True, False)]
