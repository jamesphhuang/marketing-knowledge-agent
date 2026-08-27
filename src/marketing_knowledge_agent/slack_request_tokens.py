"""Process-local, short-lived tokens standing in for a structured search request.

Slack caps a button element's ``value`` at 2000 characters. A structured search request can carry a
free-text goal far longer than that on its own, so the "調整條件" button cannot embed the request it
would reopen: Slack rejects the whole message once the limit is crossed, and truncating the request
to fit would silently reopen the modal with a *different* search than the one the user ran.

So the button carries an opaque token instead, and the request itself stays here. The store is
deliberately the smallest thing that can do that, and mirrors ``slack_pagination`` in every respect
that matters:

- it holds one already-validated ``StructuredSearchRequest`` -- taxonomy values the user picked from
  a governed option list, plus their own free-text goal. No retrieval result, no citation, no
  provenance, no Slack user identity;
- it lives in memory for one bot process. Nothing is written to SQLite, to a file, to the content
  index, or to any audit or analytics surface, and a restart simply expires every token;
- it is bounded twice, by age and by entry count, so a long-running bot cannot grow without limit;
- an expired token is never rebuilt by guessing. The modal simply reopens empty, which is always
  correct: the user can restate the search, and the alternative -- reopening a *reconstructed*
  request -- would prefill filters the user never chose.

Tokens are unguessable rather than sequential. They are not a security boundary on their own -- the
channel allowlist is re-checked at every entry point regardless -- but a guessable token would let
one channel member's button reopen another's search, and that is not worth allowing for free.
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
# one is not a practical way to reach somebody else's request.
TOKEN_BYTES = 16


@dataclass
class _StoredRequest:
    request: "StructuredSearchRequest"
    expires_at: float


class SlackRequestTokenStore:
    """Bounded, in-memory structured-search requests addressed by opaque token."""

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
        self._entries: "OrderedDict[str, _StoredRequest]" = OrderedDict()

    def store(self, request: "StructuredSearchRequest") -> str:
        """Record one request and return the token that stands for it."""
        self._expire()
        token = secrets.token_hex(TOKEN_BYTES)
        self._entries[token] = _StoredRequest(
            request=request, expires_at=self._clock() + self._ttl_seconds
        )
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return token

    def get(self, token: Optional[str]) -> Optional["StructuredSearchRequest"]:
        """The request this token stands for, or ``None`` once it has expired or never existed.

        Reading does not consume the token: a user may reopen and readjust the same search several
        times, and each reopen is the same request until its TTL runs out.
        """
        self._expire()
        entry = self._entries.get(str(token or ""))
        if entry is None:
            return None
        entry.expires_at = self._clock() + self._ttl_seconds
        self._entries.move_to_end(str(token))
        return entry.request

    def __len__(self) -> int:
        self._expire()
        return len(self._entries)

    def _expire(self) -> None:
        now = self._clock()
        for token in [token for token, entry in self._entries.items() if entry.expires_at <= now]:
            self._entries.pop(token, None)


_DEFAULT_STORE = SlackRequestTokenStore()


def default_request_token_store() -> SlackRequestTokenStore:
    """The store a bot process shares when no explicit one is injected."""
    return _DEFAULT_STORE
