"""Process-local Slack ``response_url`` capabilities, and the one transport that spends them.

A slash command reaches this bot from *any* conversation a workspace member can type in, including
ones the bot was never added to. ``chat.postEphemeral`` cannot answer those -- Human UAT proved it,
with ``channel_not_found`` on a denial that consequently never reached the user. Slack's own
mechanism for replying to an interaction regardless of membership is the ``response_url`` its
payload carries, so that is what this surface replies through.

**A response_url is a bearer secret.** Anyone holding it can post into that conversation as this
app, with no token. Everything below follows from that.

Four properties this module is responsible for, each closing a finding an independent review
reproduced against the previous candidate:

1. **Uses are spent atomically.** ``reserve()`` verifies and decrements inside one lock. The
   previous ``take()`` checked liveness and decremented as separate steps, so two threads holding
   a one-use capability could both pass the check and both send -- reproduced, 2 successes on a
   budget of 1.
2. **The destination is pinned before anything is sent.** Exact host, HTTPS, no userinfo, no
   non-443 port, no fragment -- and the transport does not follow redirects, because validating the
   first hop authorizes nothing about the second.
3. **The URL never reaches a log.** The transport is a few lines of stdlib with no logger at all,
   and every failure is re-raised as an error that carries no URL. The SDK's webhook client was
   dropped for this: it takes a logger, retries by default, and its request path can emit
   ``req.full_url``.
4. **A reservation is taken before retrieval, not checked before it.** A prior ``can_reply()`` was
   observational -- another handler could consume the last use in the window between the check and
   the send, leaving a search that had already run with nowhere to go.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlparse


# Slack documents a response_url as usable for about 30 minutes. This sits below that on purpose:
# the documented figure is when Slack stops honouring it, and expiring here first fails in our own
# code, with a message we control, instead of surfacing as a Slack error after a search has run.
DEFAULT_TTL_SECONDS = 29 * 60
# Slack documents five uses per response_url. A search spends two (result, then action message).
MAX_USES = 5
DEFAULT_MAX_ENTRIES = 200
# One reservation is one HTTP attempt. Slack may have received a request even when the client saw a
# transport error, so a failed send is never retried and never refunded -- see ``spend``.
DEFAULT_TIMEOUT_SECONDS = 10

# Exact hosts Slack serves response_urls from. An allowlist of exact hostnames, never a suffix or a
# pattern: this value arrives in a payload and is then POSTed to, so a substring match would accept
# ``hooks.slack.com.evil.test``.
ALLOWED_RESPONSE_URL_HOSTS = frozenset({"hooks.slack.com"})
# The response_url path families this surface actually receives, and nothing else.
#
# ``commands`` is what a slash-command payload carries and ``actions`` is what an interactive
# payload carries -- the two entry points this bot has. ``services`` (the incoming-webhook family)
# was in an earlier version of this list and is removed: this app never receives one, and an
# approved endpoint family that is never used is an allowance with no benefit.
#
# Compared as a whole first segment, never as a string prefix. A prefix test authorizes
# ``/commands/a/../../services/x`` on the strength of the characters it starts with, which is
# exactly the bypass an independent review reproduced.
ALLOWED_RESPONSE_URL_PATH_FAMILIES = frozenset({"commands", "actions"})
# Characters whose percent-encoded forms are refused outright, because decoding any of them
# changes what the path *means*: the separators redraw segment boundaries, and the dot builds
# relative segments. Refusing the encoded form means this validator and any downstream normaliser
# cannot disagree about the destination -- there is nothing left to normalise.
_STRUCTURAL_BYTES = frozenset(b"/\\.")

CapabilityKey = Tuple[str, str, str]


class SlackResponseUrlError(RuntimeError):
    """A response_url send failed.

    Deliberately carries no URL, no host and no response body. This is the exception that reaches
    logs, bolt's error handler and -- in the worst case -- a user-visible surface, and the thing it
    would otherwise carry is a bearer capability.
    """


def is_valid_response_url(url: object) -> bool:
    """Whether this is a Slack response_url this process may POST to.

    Every clause below rejects something an independent review demonstrated the previous, looser
    check accepted:

    - ``username``/``password`` -- ``https://user:pass@hooks.slack.com/...`` parses with the real
      host, but the credentials travel to it, and the form is a classic way to make a hostile URL
      read as a trusted one;
    - a port other than 443 -- ``hooks.slack.com:444`` is the right host and the wrong service;
    - a fragment -- Slack never sends one, so its presence means the value was constructed;
    - a path outside Slack's own roots -- a validated host with an arbitrary path is still this app
      making a request on someone else's behalf.

    ``hostname`` is compared against an exact set: it is lowercased and strips userinfo and port, so
    the comparison cannot be fooled by case, and the explicit userinfo rejection above closes the
    rest.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        port = parsed.port
    except ValueError:
        # A malformed authority such as "hooks.slack.com:notaport" raises rather than returning
        # None, and an unparseable port is not a port we may connect to.
        return False
    if port is not None and port != 443:
        return False
    if parsed.fragment:
        return False
    if parsed.hostname not in ALLOWED_RESPONSE_URL_HOSTS:
        return False
    if parsed.query:
        # Slack's response_urls carry their identity in the path. A query string is therefore
        # either unnecessary or a signal the value was constructed; neither is a reason to accept
        # one.
        return False
    return _path_is_an_approved_response_url(parsed.path)


def _path_is_an_approved_response_url(path: str) -> bool:
    """Whether this raw path is a Slack response_url endpoint, decided structurally.

    Single validation model, applied to the **raw** path exactly as it will be sent. Nothing is
    decoded and then re-checked, because a validator that normalises differently from the HTTP
    client is a validator that can be walked past -- which is how ``/commands/%2e%2e/x`` and
    ``/commands/a%2f..%2fservices/x`` were accepted before.

    Four gates, in order:

    1. every ``%`` escape is well formed, and none of them decodes to a structural byte
       (``/``, ``\`` or ``.``) or to a control character. Refusing the encoded form is what makes
       decoding irrelevant;
    2. no raw control characters or backslashes;
    3. split on ``/``: no empty segment (``//``), no ``.``, no ``..``;
    4. the first segment names an approved family, and something follows it.
    """
    if not path.startswith("/"):
        return False

    index = 0
    while index < len(path):
        char = path[index]
        if char == "%":
            escape = path[index + 1 : index + 3]
            if len(escape) != 2 or any(c not in "0123456789abcdefABCDEF" for c in escape):
                return False
            decoded = int(escape, 16)
            if decoded in _STRUCTURAL_BYTES or decoded < 0x20 or decoded == 0x7F:
                return False
            index += 3
            continue
        if char == "\\" or ord(char) < 0x20 or ord(char) == 0x7F:
            return False
        index += 1

    segments = path.split("/")[1:]
    if any(segment in ("", ".", "..") for segment in segments):
        return False
    return len(segments) >= 2 and segments[0] in ALLOWED_RESPONSE_URL_PATH_FAMILIES


class ResponseReservation:
    """One already-consumed send against a capability.

    The use it represents was decremented atomically in the store when this object was created, so
    holding one *is* the authorization to send exactly once.

    Deliberately not a dataclass. An independent review reproduced two ways the previous dataclass
    version handed out a second send from a single authorization, and both come from treating an
    authorization object as ordinary data:

    - **it was copyable.** ``copy.copy`` and ``copy.deepcopy`` produced a clone carrying the same
      bearer URL with its own fresh ``_spent = False``, so the original could send and the clone
      could send again. ``pickle`` was worse: it serialised the capability itself into bytes;
    - **the send-once transition was not atomic.** ``if not self._spent: self._spent = True`` is a
      check-then-act, so two threads handed the same object could both pass the check -- reproduced
      as two outbound requests from one reservation.

    So: ``__slots__`` (no ``__dict__`` to copy or pickle), an owned ``Lock`` guarding the
    transition, and every duplication protocol refused rather than silently allowed.
    """

    __slots__ = ("_url", "_spent", "_lock")

    def __init__(self, url: str) -> None:
        self._url = url
        self._spent = False
        self._lock = threading.Lock()

    def spend(self) -> str:
        """The URL, once, to exactly one caller.

        Check and set happen under this reservation's own lock. The store's lock does not help
        here: it protects the *budget*, and this protects the single authorization that budget
        already paid for. A loser gets an exception rather than the URL, so a second send cannot be
        attempted, let alone made.
        """
        with self._lock:
            if self._spent:
                raise SlackResponseUrlError("response capability reservation already spent")
            self._spent = True
            return self._url

    @property
    def spent(self) -> bool:
        with self._lock:
            return self._spent

    def __repr__(self) -> str:
        # Never the URL. This is what a debugger, a crash dump or a stray print would show.
        return f"<ResponseReservation spent={self._spent}>"

    # Duplication is refused rather than allowed-and-hoped-about. Each protocol below would
    # otherwise mint a second authorization from one: the copies carry the same bearer URL and a
    # fresh unspent flag, and pickling additionally writes the capability to bytes that can outlive
    # the process. The messages carry no URL.
    def __copy__(self):
        raise TypeError("a response capability reservation may not be copied")

    def __deepcopy__(self, memo):
        raise TypeError("a response capability reservation may not be deep-copied")

    def __reduce__(self):
        raise TypeError("a response capability reservation may not be serialized")

    def __getstate__(self):
        raise TypeError("a response capability reservation may not be serialized")


def single_use_reservation(response_url: str) -> Optional[ResponseReservation]:
    """One send against a capability that was never stored.

    Used by the paths that answer an interaction and start no session -- an allowlist denial, and
    the guidance a superseded button gets. There is nothing to decrement because there is no stored
    budget: the capability arrived with this one interaction and is used once, here.
    """
    if not is_valid_response_url(response_url):
        return None
    return ResponseReservation(response_url.strip())


@dataclass
class ResponseCapability:
    """One response_url and the interaction context permitted to spend it."""

    url: str = field(repr=False)
    owner_user_id: str
    channel_id: str
    session_key: str
    expires_at: float
    remaining_uses: int

    def is_live(self, now: float) -> bool:
        return self.expires_at > now and self.remaining_uses > 0


class SlackResponseUrlStore:
    """Bounded, in-memory response_url capabilities addressed by interaction context.

    Every method that reads or mutates shared state does so under ``self._lock``. That is not
    defensive style -- ``slack_bolt`` dispatches listeners on a thread pool, so two interactions for
    the same session genuinely run at once, and the previous check-then-decrement let both spend the
    same final use. The GIL does not help: the window is between two bytecode-level operations, not
    inside one.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_uses: int = MAX_USES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0 or max_uses <= 0:
            raise ValueError("ttl_seconds、max_entries 與 max_uses 必須為正數。")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._max_uses = max_uses
        # monotonic by default: a wall-clock adjustment must not resurrect or kill a capability.
        self._clock = clock
        self._entries: "OrderedDict[CapabilityKey, ResponseCapability]" = OrderedDict()
        # Reentrant so a public method may call another without deadlocking; the critical sections
        # here are a few dict operations, so contention is not a concern.
        self._lock = threading.RLock()

    def store(
        self,
        response_url: str,
        *,
        owner_user_id: str,
        channel_id: str,
        session_key: str,
    ) -> bool:
        """Record a capability for one interaction context, replacing any it already had.

        Returns ``False`` and stores nothing when the URL is not a Slack response_url or the context
        is incomplete. An empty context value would compare equal to an empty value derived from a
        malformed payload, turning the ownership check off for exactly the interactions whose
        provenance is least clear.

        Replacing is what makes a refresh work: a button click carries a newer capability than the
        command that began the session, with a longer remaining life and its own budget. A
        reservation already issued from the previous capability stays valid -- its use was consumed
        atomically when it was issued, so it is a send that has already been paid for, and revoking
        it would drop a reply the user is owed rather than prevent one they are not.
        """
        if not is_valid_response_url(response_url):
            return False
        if not all(str(v or "").strip() for v in (owner_user_id, channel_id, session_key)):
            return False

        with self._lock:
            self._expire()
            key = (str(owner_user_id), str(channel_id), str(session_key))
            self._entries.pop(key, None)
            self._entries[key] = ResponseCapability(
                url=response_url.strip(),
                owner_user_id=key[0],
                channel_id=key[1],
                session_key=key[2],
                expires_at=self._clock() + self._ttl_seconds,
                remaining_uses=self._max_uses,
            )
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return True

    def reserve(
        self, *, user_id: str, channel_id: str, session_key: str
    ) -> Optional[ResponseReservation]:
        """Claim exactly one send for this interaction, or return ``None``.

        Verification and decrement happen inside one critical section, so two threads racing for the
        last use produce exactly one reservation. This is the only way executable code may obtain a
        response capability: there is no observational "may I reply later?" that a caller could act
        on, because the answer would already be stale by the time it was used.

        Returns ``None`` -- never raises, never says why -- when there is no capability for this
        exact context, or it has expired, or its budget is gone.
        """
        with self._lock:
            self._expire()
            key = (str(user_id or ""), str(channel_id or ""), str(session_key or ""))
            entry = self._entries.get(key)
            if entry is None or not entry.is_live(self._clock()):
                return None
            entry.remaining_uses -= 1
            if entry.remaining_uses <= 0:
                self._entries.pop(key, None)
            return ResponseReservation(entry.url)

    def discard(self, *, user_id: str, channel_id: str, session_key: str) -> None:
        with self._lock:
            self._entries.pop(
                (str(user_id or ""), str(channel_id or ""), str(session_key or "")), None
            )

    def remaining_uses(self, *, user_id: str, channel_id: str, session_key: str) -> int:
        """How many sends are left for this context. Diagnostics and tests only.

        Deliberately not a routing decision: acting on this value would be the check-then-act the
        lock exists to eliminate. Executable code calls ``reserve``.
        """
        with self._lock:
            self._expire()
            entry = self._entries.get(
                (str(user_id or ""), str(channel_id or ""), str(session_key or ""))
            )
            return entry.remaining_uses if entry is not None else 0

    def __len__(self) -> int:
        with self._lock:
            self._expire()
            return len(self._entries)

    def _expire(self) -> None:
        """Drop dead entries. Callers hold the lock."""
        now = self._clock()
        for key in [k for k, e in self._entries.items() if not e.is_live(now)]:
            self._entries.pop(key, None)


_DEFAULT_STORE = SlackResponseUrlStore()


def default_response_url_store() -> SlackResponseUrlStore:
    """The store a bot process shares when no explicit one is injected."""
    return _DEFAULT_STORE


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect.

    Validating ``hooks.slack.com`` authorizes a request to ``hooks.slack.com`` and nothing else. A
    ``Location`` header is a destination chosen by the response, so following one would send a
    bearer capability wherever the responder pointed -- the host allowlist would have checked the
    hop that did not matter. Returning ``None`` makes urllib raise the original ``HTTPError``
    instead of issuing a second request.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


# One opener, built once, with the default redirect handler replaced. ``build_opener`` installs
# only the handlers it is given plus the defaults it still needs, and a handler of the same class
# replaces its default -- so no redirect can be followed through this opener.
#
# **Deployment consideration, deliberately not changed here.** ``build_opener`` also installs
# ``ProxyHandler``, which honours ``HTTPS_PROXY``/``https_proxy`` from the process environment. It
# is absent in a process with no proxy variables set -- the handler adds no methods and is dropped
# -- and present in one that has them, so whether a response_url request traverses a proxy is a
# property of the deployment, not of this code. An independent review classified this as
# non-blocking; it is recorded rather than "fixed" because pinning it would change how the bot
# behaves in a proxied network, which is an operator's decision and not this module's to make.
_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def send_response_url_message(reservation: ResponseReservation, payload: Dict[str, object]) -> None:
    """POST one message to a reserved response_url. The single outbound HTTP site on this surface.

    A few lines of stdlib rather than the Slack SDK's webhook client, for three reasons an
    independent review established against the installed 3.43.0: it accepts a ``logger`` and its
    request path can emit ``req.full_url``, which is the capability itself; it retries by default,
    so one locally accounted use could become several HTTP requests and overrun Slack's own five-use
    budget; and it exposes no way to refuse redirects.

    Exactly one HTTP attempt per reservation. A failed send is *not* refunded: Slack may have
    received and acted on the request even when the client saw a transport error, so re-spending the
    use could exceed the server-side budget and deliver the message twice.

    **On the failure path.** ``urllib``'s exceptions carry the full URL -- ``HTTPError.url``, and
    usually ``str(exc)`` -- and the sanitized error raised in their place must not carry it back
    out. ``raise ... from None`` is not enough: it clears ``__cause__`` and suppresses traceback
    rendering, but the original stays reachable through ``__context__``, which an independent review
    reproduced and which any structured error reporter that walks an exception tree will find.

    So the sanitized error is raised **outside** every ``except`` block. Nothing is being handled at
    that point, so ``__context__`` is ``None`` as well as ``__cause__``. Only a fixed string and an
    integer status cross the boundary, and the URL and request locals are deleted first so they
    cannot be recovered from this frame by a reporter that serialises locals.
    """
    url = reservation.spend()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    # Carries nothing but a fixed message; deliberately not the exception itself.
    failure: Optional[str] = None
    try:
        with _OPENER.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            if status is not None and not 200 <= int(status) < 300:
                failure = f"Slack response_url 回應非成功狀態：{int(status)}"
    except urllib.error.HTTPError as exc:
        # Covers the refused redirect too: _NoRedirectHandler leaves the 3xx as an HTTPError.
        failure = f"Slack response_url 回應非成功狀態：{exc.code}"
    except Exception as exc:  # noqa: BLE001 - only the type name is kept, never the URL
        failure = f"Slack response_url 傳送失敗：{type(exc).__name__}"

    if failure is not None:
        # Outside the handler, so no exception is active and the sanitized error inherits no
        # context. The locals holding the capability go first, for reporters that walk frames.
        del url, request
        raise SlackResponseUrlError(failure)
