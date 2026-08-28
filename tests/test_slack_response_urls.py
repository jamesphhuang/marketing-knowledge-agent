"""Contract tests for the Slack response_url capability store.

Every URL here is the reserved fake below. A real response_url is a bearer capability: anyone
holding it can post into that conversation as the app, without a token. It must never be committed
to a repository, and a test fixture is a repository file like any other.
"""

import pytest

from marketing_knowledge_agent.slack_response_urls import (
    MAX_USES,
    ResponseCapability,
    SlackResponseUrlStore,
    is_valid_response_url,
)


FAKE_URL = "https://hooks.slack.com/commands/TEST/SECRET_CAPABILITY"
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
        "https://hooks.slack-gov.com/commands/T/1/x",
    ],
)
def test_slack_response_urls_are_accepted(url):
    assert is_valid_response_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/commands/T/1/x",     # plaintext
        "https://evil.test/commands/T/1/x",          # wrong host entirely
        "https://hooks.slack.com.evil.test/x",       # suffix that only looks like the real host
        "https://hooks.slack.com@evil.test/x",       # userinfo pointing somewhere else
        "https://evil.test/?u=https://hooks.slack.com/x",
        "ftp://hooks.slack.com/x",
        "//hooks.slack.com/x",
        "", "   ", None, 42, b"https://hooks.slack.com/x",
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
    assert store.store("https://evil.test/x", **CTX) is False
    assert len(store) == 0
    assert store.can_reply(**CLICK) is False


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
    assert store.take(**CLICK) == FAKE_URL


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

    assert store.take(**wrong) is None
    assert store.can_reply(**wrong) is False
    # And it did not spend the owner's budget on the way.
    assert store.remaining_uses(**CLICK) == MAX_USES


def test_an_unknown_context_is_indistinguishable_from_a_wrong_one():
    store = SlackResponseUrlStore()
    assert store.take(**CLICK) is None
    store.store(FAKE_URL, **CTX)
    assert store.take(user_id="U2", channel_id="C1", session_key="U1:sess") is None


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
        assert store.take(**CLICK) == FAKE_URL

    assert store.take(**CLICK) is None
    assert store.can_reply(**CLICK) is False


def test_the_ttl_sits_below_slacks_documented_lifetime():
    from marketing_knowledge_agent.slack_response_urls import DEFAULT_TTL_SECONDS

    assert 0 < DEFAULT_TTL_SECONDS < 30 * 60


def test_an_expired_capability_refuses_locally():
    clock = [1000.0]
    store = SlackResponseUrlStore(ttl_seconds=60, clock=lambda: clock[0])
    store.store(FAKE_URL, **CTX)

    assert store.can_reply(**CLICK) is True
    clock[0] += 61
    assert store.can_reply(**CLICK) is False
    assert store.take(**CLICK) is None


# --------------------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------------------


def test_storing_again_replaces_the_capability_and_restores_the_budget():
    """A button click carries a newer capability than the command that began the session."""
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)
    store.take(**CLICK)
    assert store.remaining_uses(**CLICK) == MAX_USES - 1

    assert store.store(OTHER_FAKE_URL, **CTX) is True

    assert store.remaining_uses(**CLICK) == MAX_USES
    assert store.take(**CLICK) == OTHER_FAKE_URL


def test_a_refresh_with_an_invalid_url_leaves_the_existing_capability_alone():
    store = SlackResponseUrlStore()
    store.store(FAKE_URL, **CTX)

    assert store.store("https://evil.test/x", **CTX) is False

    assert store.take(**CLICK) == FAKE_URL


def test_capabilities_are_bounded_by_entry_count():
    store = SlackResponseUrlStore(max_entries=2)
    for i in range(4):
        store.store(FAKE_URL, owner_user_id=f"U{i}", channel_id="C1", session_key=f"U{i}:s")
    assert len(store) == 2
