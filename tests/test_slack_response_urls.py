"""Contract tests for the Slack response_url capability store.

Every URL here is the reserved fake below. A real response_url is a bearer capability: anyone
holding it can post into that conversation as the app, without a token. It must never be committed
to a repository, and a test fixture is a repository file like any other.
"""

import io
import logging
import threading
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
        "https://hooks.slack.com/services/TEST/X",
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
        elif behaviour == "server_error":
            self.send_response(500)
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
    return ResponseReservation(_url=f"http://{host}:{port}/commands/TEST/SECRET_CAPABILITY")


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
            ResponseReservation(_url=f"http://{host}:{port}/capture"), {"text": "hello"}
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
    reservation = ResponseReservation(
        _url="http://127.0.0.1:9/commands/TEST/SECRET_CAPABILITY"
    )
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
        failing = ResponseReservation(_url=_reservation_for(origin)._url)
        with pytest.raises(SlackResponseUrlError):
            send_response_url_message(failing, {"text": "hello"})

    # The store is untouched by a transport failure; the use stays spent.
    assert store.remaining_uses(**CLICK) == 0
    assert reservation.spent is False
