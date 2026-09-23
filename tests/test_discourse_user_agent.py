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
