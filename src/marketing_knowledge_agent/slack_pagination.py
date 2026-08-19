"""Process-local continuation state for paginated Slack search results.

A search whose result does not fit one Slack message leaves its remaining pages here, so that a
「顯示更多」 reply in the same thread can continue where the first message stopped. The store is
deliberately the smallest thing that can do that:

- it holds already-rendered, already-governed user-facing text -- no query, no query plan, no
  citation, no provenance, no metadata and no Slack user identity;
- it is keyed only on the technical routing coordinates needed to answer in the right thread;
- it lives in memory for one bot process. Nothing is written to SQLite, to a file, to the content
  index or to any audit or analytics surface, and a restart simply expires every continuation;
- it is bounded twice, by age and by entry count, so a long-running bot cannot grow without limit.

It is not conversation memory and not a search history: nothing here can reconstruct what a user
searched for, and a continuation that has expired is never rebuilt by guessing or re-querying.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple


# A continuation is browsed within seconds or a couple of minutes of the first message; a quarter
# of an hour covers a distracted reader while keeping result text out of memory the rest of the
# day. Expiry is never an error the user has to understand -- it fails closed into "run the search
# again", which is always correct because the search itself is cheap and offline.
DEFAULT_TTL_SECONDS = 900
# Each entry holds one search's remaining pages. A few hundred concurrent threads is far beyond
# what a single channel-restricted bot sees, and the oldest entry is evicted past that.
DEFAULT_MAX_ENTRIES = 200


PaginationKey = Tuple[str, str]


def pagination_key(channel_id: str, thread_ts: str) -> PaginationKey:
    """The routing coordinates of one Slack thread -- never the user who posted in it."""
    return (str(channel_id or ""), str(thread_ts or ""))


@dataclass
class _Continuation:
    pages: Tuple[str, ...]
    next_index: int
    expires_at: float


class SlackPaginationStore:
    """Bounded, in-memory continuations keyed by (channel, thread)."""

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
        # monotonic by default: a wall-clock adjustment must not resurrect or kill a continuation.
        self._clock = clock
        self._entries: "OrderedDict[PaginationKey, _Continuation]" = OrderedDict()

    def start(self, key: PaginationKey, pages: Sequence[str]) -> None:
        """Record the pages after the first one, replacing whatever this thread held before.

        A new search in a thread always wins: the thread's 「顯示更多」 continues the newest
        search, never an older one. A result that fits one page stores nothing and clears the
        thread instead, so a stale continuation can never be resumed under a fresh search.
        """
        self._expire()
        self._entries.pop(key, None)
        remaining = tuple(pages)[1:]
        if not remaining:
            return
        self._entries[key] = _Continuation(
            pages=remaining, next_index=0, expires_at=self._clock() + self._ttl_seconds
        )
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def next_page(self, key: PaginationKey) -> Optional[str]:
        """The next page for this thread, or None when there is no live continuation left."""
        self._expire()
        entry = self._entries.get(key)
        if entry is None:
            return None
        page = entry.pages[entry.next_index]
        entry.next_index += 1
        if entry.next_index >= len(entry.pages):
            self._entries.pop(key, None)
        else:
            # Reading keeps the continuation alive and marks it as the most recently used, so
            # eviction pressure falls on threads nobody is browsing.
            entry.expires_at = self._clock() + self._ttl_seconds
            self._entries.move_to_end(key)
        return page

    def discard(self, key: PaginationKey) -> None:
        self._entries.pop(key, None)

    def __len__(self) -> int:
        self._expire()
        return len(self._entries)

    def _expire(self) -> None:
        now = self._clock()
        for key in [key for key, entry in self._entries.items() if entry.expires_at <= now]:
            self._entries.pop(key, None)


_DEFAULT_STORE = SlackPaginationStore()


def default_pagination_store() -> SlackPaginationStore:
    """The store a bot process shares when no explicit one is injected."""
    return _DEFAULT_STORE
