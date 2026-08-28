"""Process-local continuation state for paginated Slack search results.

A search whose result does not fit one Slack message leaves its remaining pages here, so that a
「顯示更多」 reply in the same thread can continue where the first message stopped. The store is
deliberately the smallest thing that can do that:

- it holds already-rendered, already-governed user-facing text -- no query, no query plan, no
  citation, no provenance, no metadata;
- it is keyed only on the technical routing coordinates needed to answer in the right place. In the
  ``app_mention`` flow that is the channel and thread. In the ``/mka`` slash flow there is no
  thread -- a slash command is not a message -- so the second coordinate is a per-invocation
  session key which, because the result is ephemeral and addressed to exactly one person, is bound
  to the invoking user. The key is still opaque routing data to this module: it stores no identity
  of its own and never reads one back out;
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
# Each entry holds one search's remaining pages. A few hundred concurrent lanes is far beyond
# what a single channel-restricted bot sees, and the oldest entry is evicted past that.
DEFAULT_MAX_ENTRIES = 200


PaginationKey = Tuple[str, str]


def pagination_key(channel_id: str, session_key: str) -> PaginationKey:
    """The routing coordinates of one continuation lane.

    ``session_key`` is whatever the calling entry point uses to separate one search from the next:
    a ``thread_ts`` for the ``app_mention`` flow, a per-invocation session key for the ``/mka``
    slash flow. This module attaches no meaning to it beyond equality.
    """
    return (str(channel_id or ""), str(session_key or ""))


@dataclass
class _Continuation:
    pages: Tuple[str, ...]
    next_index: int
    expires_at: float


class SlackPaginationStore:
    """Bounded, in-memory continuations keyed by (channel, session)."""

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
        """Record the pages after the first one, replacing whatever this lane held before.

        A new search in a lane always wins: 「顯示更多」 continues the newest search, never an
        older one. A result that fits one page stores nothing and clears the lane instead, so a
        stale continuation can never be resumed under a fresh search.
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
        """The next page for this lane, or None when there is no live continuation left."""
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
            # eviction pressure falls on lanes nobody is browsing.
            entry.expires_at = self._clock() + self._ttl_seconds
            self._entries.move_to_end(key)
        return page

    def has_more(self, key: PaginationKey) -> bool:
        """Whether this lane still holds an unread page.

        A pure query: it never advances, refreshes or evicts a continuation. ``next_page`` drops
        the entry once it hands out the last page, so presence here is exactly "another page is
        waiting" -- which is what decides whether a 「顯示更多」 button is offered at all. Offering
        one that answers "已失效" would be worse than offering none.
        """
        self._expire()
        return key in self._entries

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
