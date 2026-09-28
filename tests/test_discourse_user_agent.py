"""Every client that talks to Discourse must identify itself the same way.

The forum sits behind Cloudflare, which refuses urllib's default signature
outright:

    Error 1010: Access denied -- the site owner has blocked access based on
    your browser's signature.

The provider had always set a User-Agent. A second HTTP client, written later
for the content migration, did not -- so every one of its calls failed with a
403 that reads like Discourse saying no, while the first client carried on
working. One shared constant, and a test, so a third client cannot repeat it.
"""
import inspect

import pytest

from aeronautics_members import forum_service
from aeronautics_members.forum_service import DISCOURSE_USER_AGENT, DiscourseConnectProvider
from aeronautics_members.services import forum_content
from aeronautics_members.services.forum_content import ContentPoster

SETTINGS = {
    "forum_base_url": "https://forum.test",
    "discourse_api_key": "k",
    "discourse_api_username": "system",
    "discourse_connect_secret": "s",
}


def _headers_from(client_call, monkeypatch):
    """The headers a client actually puts on the wire."""
    seen = {}

    class _Response:
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *args): return False

    def fake_urlopen(request, timeout=None):
        seen.update({k.lower(): v for k, v in request.header_items()})
        return _Response()

    monkeypatch.setattr(forum_service, "urlopen", fake_urlopen)
    monkeypatch.setattr(forum_content, "urlopen", fake_urlopen)
    client_call()
    return seen


def test_the_provider_identifies_itself(monkeypatch):
    provider = DiscourseConnectProvider(SETTINGS)

    headers = _headers_from(lambda: provider._request("GET", "/site.json"), monkeypatch)

    assert headers.get("User-agent".lower()) == DISCOURSE_USER_AGENT


def test_the_content_poster_identifies_itself_the_same_way(monkeypatch):
    """This is the one that failed every call against the real forum."""
    poster = ContentPoster(SETTINGS)

    headers = _headers_from(
        lambda: poster._call("GET", "/posts/1.json"), monkeypatch
    )

    assert headers.get("User-agent".lower()) == DISCOURSE_USER_AGENT


def test_it_is_not_the_default_python_signature():
    """Which is the exact string Cloudflare is configured to refuse."""
    assert "python" not in DISCOURSE_USER_AGENT.lower()
    assert "urllib" not in DISCOURSE_USER_AGENT.lower()


def test_nobody_writes_their_own():
    """A hard-coded one in a new client is how this happened."""
    for module in (forum_service, forum_content):
        source = inspect.getsource(module)
        for line in source.splitlines():
            if '"User-Agent"' in line or "'User-Agent'" in line:
                assert "DISCOURSE_USER_AGENT" in line, (
                    f"{module.__name__} sets a User-Agent of its own: {line.strip()}"
                )


class TestWhenTheKeyMayNotActAsSomebodyElse:
    """Discourse API keys carry a user level.

    One bound to a single user works for every admin call -- they all act as
    that user -- and fails the instant it is asked to act as somebody else,
    which is the whole of the content migration. The raw error says only "The
    API username or key is invalid", which sounds like a wrong key.
    """

    def _poster_that_gets(self, monkeypatch, code, body):
        import io
        from urllib.error import HTTPError

        def fake_urlopen(request, timeout=None):
            raise HTTPError("https://forum.test/posts.json", code, "Forbidden", {},
                            io.BytesIO(body.encode()))

        monkeypatch.setattr(forum_content, "urlopen", fake_urlopen)
        return ContentPoster(SETTINGS)

    def test_it_explains_what_to_change(self, monkeypatch):
        poster = self._poster_that_gets(
            monkeypatch, 403,
            '{"errors":["The API username or key is invalid."],'
            '"error_type":"invalid_access"}',
        )

        with pytest.raises(Exception) as raised:
            poster._call("POST", "/posts.json", as_username="SpaniolA_L18")

        message = str(raised.value)
        assert "SpaniolA_L18" in message
        assert "All Users" in message
        assert "revoke" in message, "a key that can act as anybody should not linger"

    def test_an_ordinary_failure_is_still_reported_plainly(self, monkeypatch):
        """Only the impersonation case gets the explanation."""
        poster = self._poster_that_gets(monkeypatch, 422, '{"errors":["Title too short"]}')

        with pytest.raises(Exception) as raised:
            poster._call("POST", "/posts.json", as_username="SpaniolA_L18")

        assert "Title too short" in str(raised.value)
        assert "All Users" not in str(raised.value)
