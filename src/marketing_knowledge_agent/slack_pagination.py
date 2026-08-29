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

import secrets
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
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
# 32 hex characters identifying one search's continuation. Opaque and server-minted: it carries no
# query, no conditions and nothing a user typed, and it never authorizes anything on its own --
# ownership is still the interaction's user, channel and session plus the request token.
GENERATION_BYTES = 16


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
    generation: str
    pages: Tuple[str, ...]
    next_index: int
    expires_at: float


class SlackPaginationStore:
    """Bounded, in-memory continuations keyed by (channel, session), versioned by generation.

    Two properties this store has to provide, and the second is why it looks like this.

    **A new search supersedes the old one.** That was already true sequentially, and an independent
    review showed it was not true under concurrency: a 「顯示更多」 worker that had already read an
    entry kept operating on it while a new search installed a replacement, then delivered a page
    from the superseded result and -- on a last page -- ran an unconditional ``pop`` that deleted
    the *new* continuation. Same user, so ownership checks never applied.

    So every continuation carries an opaque **generation**, and every operation names the generation
    it believes it is working on. A stale generation reads nothing, advances nothing and removes
    nothing.

    **Delivery is ordered against supersession.** Generation checks alone still allow "worker reads
    a valid page, new search installs, worker then sends" -- the check passed when it was made. A
    per-lane guard closes that: ``lane_operation`` serialises a consume-and-deliver against
    ``start`` for the *same* lane, so the two possible orders are "old page, then new search" and
    "new search, then the stale click refuses". A new search followed by an old page is not
    reachable.

    The guard is per lane, not global, so unrelated users and channels never wait on each other --
    and the network send happens inside the caller's guarded block rather than inside this store,
    which owns continuation state and not Slack transport.
    """

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
        # Guards the entry map itself. Reentrant so a public method may call another.
        self._lock = threading.RLock()
        # One guard per lane, for serialising a consume-and-deliver against a supersede. Bounded
        # the same way the entries are; evicting the least-recently-created lock cannot corrupt an
        # operation already holding it, because that holder owns the object it acquired.
        self._lane_locks: "OrderedDict[PaginationKey, threading.RLock]" = OrderedDict()

    def _lane_lock(self, key: PaginationKey) -> "threading.RLock":
        with self._lock:
            lock = self._lane_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._lane_locks[key] = lock
                while len(self._lane_locks) > self._max_entries:
                    self._lane_locks.popitem(last=False)
            else:
                self._lane_locks.move_to_end(key)
            return lock

    @contextmanager
    def lane_operation(self, key: PaginationKey):
        """Serialise one lane's work against any other operation on that same lane.

        Held across the caller's delivery, which is the point: a page must not be sent after the
        search it belongs to has been superseded. Scoped to one lane so unrelated conversations are
        never blocked, and deliberately not held by this store during the send -- the caller does
        the sending, this only decides when a supersede may interleave.
        """
        with self._lane_lock(key):
            yield

    def start(self, key: PaginationKey, pages: Sequence[str]) -> str:
        """Install the pages after the first one as a new generation, and return its id.

        A new search in a lane always wins: 「顯示更多」 continues the newest search, never an
        older one. A result that fits one page installs no continuation and clears the lane, so a
        stale button cannot resume the search the user has moved on from -- the generation is still
        minted and returned, so the caller can label its buttons consistently either way.
        """
        generation = secrets.token_hex(GENERATION_BYTES)
        with self._lane_lock(key):
            with self._lock:
                self._expire()
                self._entries.pop(key, None)
                remaining = tuple(pages)[1:]
                if remaining:
                    self._entries[key] = _Continuation(
                        generation=generation,
                        pages=remaining,
                        next_index=0,
                        expires_at=self._clock() + self._ttl_seconds,
                    )
                    while len(self._entries) > self._max_entries:
                        self._entries.popitem(last=False)
        return generation

    def consume_next_page(self, key: PaginationKey, generation: str) -> Optional[str]:
        """The next page of *this* generation, or ``None``.

        ``None`` covers "no continuation", "expired" and "you are asking about a search that has
        been superseded" without distinguishing them: to the clicker they are the same event, and
        the answer is the same either way.

        The last page removes the entry only when it is still this generation's -- the unconditional
        ``pop`` it replaces is what let a stale worker delete a newer search's continuation.
        """
        with self._lock:
            self._expire()
            entry = self._entries.get(key)
            if entry is None or entry.generation != generation:
                return None
            page = entry.pages[entry.next_index]
            entry.next_index += 1
            if entry.next_index >= len(entry.pages):
                self._remove_if_current(key, generation)
            else:
                # Reading keeps the continuation alive and marks it most recently used, so eviction
                # pressure falls on lanes nobody is browsing.
                entry.expires_at = self._clock() + self._ttl_seconds
                self._entries.move_to_end(key)
            return page

    def consume_current_generation(self, key: PaginationKey) -> Optional[str]:
        """The next page of whichever generation this lane currently holds.

        For the ``app_mention`` flow, whose 「顯示更多」 is a thread reply rather than a button and
        so carries no generation of its own. Resolving the generation and consuming it happen in one
        critical section, so this cannot advance or delete a continuation installed after the read
        -- the specific failure that made the unconditional ``pop`` dangerous.
        """
        with self._lock:
            self._expire()
            entry = self._entries.get(key)
            if entry is None:
                return None
            return self.consume_next_page(key, entry.generation)

    def has_more(self, key: PaginationKey, generation: str) -> bool:
        """Whether *this* generation still holds an unread page.

        Generation-aware for the same reason ``consume_next_page`` is: a stale worker asking a
        key-only question would otherwise observe a newer search's continuation and offer a button
        that advances someone else's result.
        """
        with self._lock:
            self._expire()
            entry = self._entries.get(key)
            return entry is not None and entry.generation == generation

    def discard(self, key: PaginationKey) -> None:
        """Drop whatever this lane holds. Unconditional by design -- used when superseding."""
        with self._lock:
            self._entries.pop(key, None)

    def _remove_if_current(self, key: PaginationKey, generation: str) -> None:
        """Remove this lane's entry only if it is still the generation the caller was working on."""
        entry = self._entries.get(key)
        if entry is not None and entry.generation == generation:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            self._expire()
            return len(self._entries)

    def _expire(self) -> None:
        """Drop dead entries. Callers hold ``self._lock``."""
        now = self._clock()
        for key in [key for key, entry in self._entries.items() if entry.expires_at <= now]:
            self._entries.pop(key, None)


_DEFAULT_STORE = SlackPaginationStore()


def default_pagination_store() -> SlackPaginationStore:
    """The store a bot process shares when no explicit one is injected."""
    return _DEFAULT_STORE
