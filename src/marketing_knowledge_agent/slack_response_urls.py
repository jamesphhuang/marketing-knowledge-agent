"""Process-local, short-lived Slack ``response_url`` capabilities.

A slash command is delivered to this bot from *any* conversation a workspace member can type in,
including ones the bot was never added to. ``chat.postEphemeral`` cannot answer those: Slack
requires the app to be able to post into the target conversation, and returns ``channel_not_found``
otherwise. Human UAT hit exactly that -- a ``/mka`` from outside the operator's allowlist entered
the denial path correctly and then failed to deliver the denial, so the user saw nothing at all.

Slack's own answer for this is the ``response_url`` every command and interaction payload carries.
It is a callback capability tied to that one interaction, it answers ephemerally by default, and it
does not depend on channel membership. So it is what this surface replies through.

**A response_url is a bearer secret.** Anyone holding it can post into that conversation as this
app, without a token. It is therefore treated the way a credential is, not the way a routing
coordinate is:

- it lives in this process's memory and nowhere else. It is never written to ``private_metadata``,
  a button ``value``, a request token, a pagination key, an audit row, a log line, an exception
  message, or any file;
- it is excluded from ``repr``, so it cannot leak through a debugger, a crash dump or a stray
  ``print`` of the record that holds it;
- it is never handed to a caller by lookup alone. ``take()`` returns it only for an interaction
  that matches the user, channel and session it was minted for, and spends one use in doing so;
- it is bounded twice by Slack's own contract -- a TTL below the documented lifetime, and a hard
  send budget -- so an expired or exhausted capability fails closed here rather than at Slack.

Unknown, expired, wrong-user, wrong-channel, wrong-session and exhausted are all reported the same
way: ``None``. Distinguishing them would tell a caller which half of a guess was right.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse


# Slack documents a response_url as usable for about 30 minutes. This sits deliberately below that:
# the documented figure is when Slack stops honouring it, and a capability that expires here first
# fails closed in our own code, with a message we control, instead of surfacing as a Slack error
# after a search has already run.
DEFAULT_TTL_SECONDS = 29 * 60
# Slack documents five uses per response_url. A search spends two (the result page, then the action
# message), so this bounds retries and refreshes without ever silently exceeding the platform.
MAX_USES = 5
# Enough concurrent /mka sessions for a channel-scoped bot several times over; the oldest is
# evicted past this so a long-running process cannot grow without limit.
DEFAULT_MAX_ENTRIES = 200

# Exact hosts Slack serves response_urls from. An allowlist of hosts rather than a pattern, because
# this value arrives in a payload and is then POSTed to: accepting an arbitrary HTTPS host would
# turn this module into a request-forgery primitive aimed at whatever an attacker could get into a
# payload. Commercial Slack is the deployment target; the GovSlack host is listed because including
# it costs nothing and keeps the check exact rather than tempting a future wildcard.
ALLOWED_RESPONSE_URL_HOSTS = frozenset({"hooks.slack.com", "hooks.slack-gov.com"})

CapabilityKey = Tuple[str, str, str]


def is_valid_response_url(url: object) -> bool:
    """Whether this is a Slack response_url this process may POST to.

    Checked before storing rather than before sending, so a malformed or hostile value is refused
    at the boundary where it enters, not after it has been carried around.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    # ``hostname`` is lowercased and strips any userinfo/port, so "hooks.slack.com@evil.test" and
    # "HOOKS.SLACK.COM" cannot slip through a naive string comparison.
    return parsed.hostname in ALLOWED_RESPONSE_URL_HOSTS


@dataclass
class ResponseCapability:
    """One response_url and the interaction context permitted to spend it."""

    # repr=False so the secret cannot escape through a debugger, a crash dump, or any code that
    # prints the record. Nothing outside this module reads the attribute directly.
    url: str = field(repr=False)
    owner_user_id: str
    channel_id: str
    session_key: str
    expires_at: float
    remaining_uses: int

    def is_live(self, now: float) -> bool:
        return self.expires_at > now and self.remaining_uses > 0


class SlackResponseUrlStore:
    """Bounded, in-memory response_url capabilities addressed by interaction context."""

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

    def store(
        self,
        response_url: str,
        *,
        owner_user_id: str,
        channel_id: str,
        session_key: str,
    ) -> bool:
        """Record a capability for one interaction context, replacing any it already had.

        Returns ``False`` and stores nothing when the URL is not a Slack response_url or the
        context is incomplete. An empty context value would compare equal to an empty value derived
        from a malformed payload, turning the ownership check off for exactly the interactions
        whose provenance is least clear.

        Replacing rather than appending is what makes a refresh work: an action carries a newer
        response_url than the command that started the session, and the newer one has the longer
        remaining life.
        """
        if not is_valid_response_url(response_url):
            return False
        if not all(str(v or "").strip() for v in (owner_user_id, channel_id, session_key)):
            return False

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

    def take(
        self, *, user_id: str, channel_id: str, session_key: str
    ) -> Optional[str]:
        """The response_url for this interaction, spending one of its uses.

        Returns ``None`` -- never raises, never says why -- when there is no capability for this
        exact context, or it has expired, or its send budget is gone. The caller treats all of
        those as "there is no safe way to reply", which is the only distinction that matters.
        """
        self._expire()
        entry = self._entries.get(
            (str(user_id or ""), str(channel_id or ""), str(session_key or ""))
        )
        if entry is None or not entry.is_live(self._clock()):
            return None
        entry.remaining_uses -= 1
        if entry.remaining_uses <= 0:
            self._entries.pop(
                (entry.owner_user_id, entry.channel_id, entry.session_key), None
            )
        return entry.url

    def can_reply(self, *, user_id: str, channel_id: str, session_key: str) -> bool:
        """Whether a reply path exists, without spending it.

        Used before running a search, so a query is never executed when its result could not be
        delivered afterwards.
        """
        self._expire()
        entry = self._entries.get(
            (str(user_id or ""), str(channel_id or ""), str(session_key or ""))
        )
        return entry is not None and entry.is_live(self._clock())

    def discard(self, *, user_id: str, channel_id: str, session_key: str) -> None:
        self._entries.pop(
            (str(user_id or ""), str(channel_id or ""), str(session_key or "")), None
        )

    def remaining_uses(self, *, user_id: str, channel_id: str, session_key: str) -> int:
        """How many sends are left for this context. Diagnostics and tests only."""
        self._expire()
        entry = self._entries.get(
            (str(user_id or ""), str(channel_id or ""), str(session_key or ""))
        )
        return entry.remaining_uses if entry is not None else 0

    def __len__(self) -> int:
        self._expire()
        return len(self._entries)

    def _expire(self) -> None:
        now = self._clock()
        for key in [k for k, e in self._entries.items() if not e.is_live(now)]:
            self._entries.pop(key, None)


_DEFAULT_STORE = SlackResponseUrlStore()


def default_response_url_store() -> SlackResponseUrlStore:
    """The store a bot process shares when no explicit one is injected."""
    return _DEFAULT_STORE
