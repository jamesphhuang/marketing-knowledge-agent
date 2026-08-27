"""Process-local, short-lived tokens standing in for a structured search request.

Slack caps a button element's ``value`` at 2000 characters. A structured search request can carry a
free-text goal far longer than that on its own, so the "調整條件" button cannot embed the request it
would reopen: Slack rejects the whole message once the limit is crossed, and truncating the request
to fit would silently reopen the modal with a *different* search than the one the user ran.

So the button carries an opaque token, and the request itself stays here.

**A token is not a capability.** The button carrying it is posted into a channel, where everyone who
can see the thread can click it. If a token resolved on presentation alone, any channel member could
reopen somebody else's modal and read their filters and their free-text goal -- which is private
search intent, typed into what looks like a private dialog. So a token resolves only when the
*interaction* matches the context the token was minted in: same user, same channel, same thread. A
mismatch is not an error to report back to the clicker (that would confirm the token exists); it
simply resolves to nothing and the modal opens empty.

The store is otherwise the smallest thing that can do this, and mirrors ``slack_pagination``:

- it holds one already-validated ``StructuredSearchRequest`` plus the context that owns it. No
  retrieval result, no citation, no provenance;
- it lives in memory for one bot process. Nothing is written to SQLite, to a file, to the content
  index, or to any audit or analytics surface, and a restart simply expires every token;
- it is bounded twice, by age and by entry count, so a long-running bot cannot grow without limit;
- an expired or non-matching token is never rebuilt by guessing. The modal reopens empty, which is
  always correct: the user can restate the search, whereas reopening a *reconstructed* request would
  prefill filters the user never chose.

A refused query must never reach this store at all -- see the denylist-refusal path in
``slack_interface``. Restricted text that is kept anywhere shared is the thing the refusal exists to
prevent, and this store is shared across every viewer of a channel.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .structured_search import StructuredSearchRequest


# A token is spent within seconds or minutes of the result message it accompanies. A quarter of an
# hour covers a distracted reader while keeping request text out of memory the rest of the day --
# the same reasoning, and the same number, as the pagination store's continuation TTL.
DEFAULT_TTL_SECONDS = 900
DEFAULT_MAX_ENTRIES = 200
# 32 hex characters. Far below Slack's 2000-character button budget, and wide enough that guessing
# one is not a practical way to reach somebody else's request. Unguessability is defence in depth
# only: the context match below is what actually stops cross-user access.
TOKEN_BYTES = 16


@dataclass
class RequestContext:
    """One stored request and the interaction context permitted to reopen it."""

    request: "StructuredSearchRequest"
    owner_user_id: str
    channel_id: str
    thread_ts: str
    expires_at: float

    def matches(self, *, user_id: str, channel_id: str, thread_ts: str) -> bool:
        """Whether this interaction may reopen this request.

        All three must match. Channel and thread are checked as well as the owner because the same
        person can be in several conversations at once, and a token minted in one thread has no
        business reopening in another -- that would move one conversation's search intent into a
        different audience.
        """
        return (
            self.owner_user_id == user_id
            and self.channel_id == channel_id
            and self.thread_ts == thread_ts
        )


class SlackRequestTokenStore:
    """Bounded, in-memory structured-search requests addressed by opaque token plus context."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("ttl_seconds 與 max_entries 必須為正數。")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        # monotonic by default: a wall-clock adjustment must not resurrect or kill a token.
        self._clock = clock
        self._entries: "OrderedDict[str, RequestContext]" = OrderedDict()

    def store(
        self,
        request: "StructuredSearchRequest",
        *,
        owner_user_id: str,
        channel_id: str,
        thread_ts: str,
    ) -> str:
        """Record one request against the context that owns it, and return its token.

        The three context values are required and must be non-empty. An empty one would compare
        equal to an empty value derived from a malformed interaction payload, turning the context
        check into a no-op for exactly the requests whose provenance is least clear.
        """
        missing = [
            name
            for name, value in (
                ("owner_user_id", owner_user_id),
                ("channel_id", channel_id),
                ("thread_ts", thread_ts),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise ValueError(
                f"request token 需要完整的互動 context；缺少：{', '.join(missing)}。"
            )

        self._expire()
        token = secrets.token_hex(TOKEN_BYTES)
        self._entries[token] = RequestContext(
            request=request,
            owner_user_id=str(owner_user_id),
            channel_id=str(channel_id),
            thread_ts=str(thread_ts),
            expires_at=self._clock() + self._ttl_seconds,
        )
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return token

    def resolve(
        self,
        token: Optional[str],
        *,
        user_id: str,
        channel_id: str,
        thread_ts: str,
    ) -> Optional["StructuredSearchRequest"]:
        """The request this token stands for, for this interaction only.

        Returns ``None`` -- never raises, never partially discloses -- when the token is unknown,
        expired, or was minted in a different user/channel/thread context. The caller opens an empty
        modal in every one of those cases, so a clicker cannot tell "not yours" from "expired".

        Resolving does not consume the token: the owner may reopen and readjust the same search
        several times, and each reopen is the same request until its TTL runs out.
        """
        self._expire()
        entry = self._entries.get(str(token or ""))
        if entry is None:
            return None
        if not entry.matches(
            user_id=str(user_id or ""),
            channel_id=str(channel_id or ""),
            thread_ts=str(thread_ts or ""),
        ):
            return None
        entry.expires_at = self._clock() + self._ttl_seconds
        self._entries.move_to_end(str(token))
        return entry.request

    def __len__(self) -> int:
        self._expire()
        return len(self._entries)

    def __contains__(self, token: object) -> bool:
        """Whether a token is currently held, ignoring context. For diagnostics and tests only."""
        self._expire()
        return str(token or "") in self._entries

    def stored_requests(self) -> tuple:
        """Every request currently held, for assertions about what the store retains."""
        self._expire()
        return tuple(entry.request for entry in self._entries.values())

    def _expire(self) -> None:
        now = self._clock()
        for token in [token for token, entry in self._entries.items() if entry.expires_at <= now]:
            self._entries.pop(token, None)


_DEFAULT_STORE = SlackRequestTokenStore()


def default_request_token_store() -> SlackRequestTokenStore:
    """The store a bot process shares when no explicit one is injected."""
    return _DEFAULT_STORE
