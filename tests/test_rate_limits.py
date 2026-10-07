"""Rate limits that stop password guessing without stopping an intake evening.

Every limit used to count per network address. Students signing up or logging
in together on the campus network share one address, so the eleventh login in
a quarter of an hour -- anybody's -- was refused. Now the tight limit counts
attempts at one email address from one network, and a loose one counts the
whole network.

The store the counts live in (Redis) must not be able to take logging in down
with it either: if it is unreachable, the limits fall back to memory.
"""
import pytest

from conftest import app_module, db, make_member


def _reset_counts():
    try:
        app_module.limiter.reset()
    except Exception:  # noqa: BLE001 -- a store that is down has nothing to reset
        pass


def _limited_app(tmp_path, monkeypatch, storage_uri):
    """The app with rate limiting switched on, counting in ``storage_uri``."""
    import os

    monkeypatch.setattr(app_module.limiter, "_storage_uri", storage_uri)
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'limits.db'}"
    application = app_module.create_app(config_overrides={
        "TESTING": True,
        "SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": True,
        "PUBLIC_BASE_URL": "https://members.test",
        "BACKUP_DIR": str(tmp_path / "backups"),
    })
    with application.app_context():
        db.create_all()
        _reset_counts()
        try:
            yield application
        finally:
            _reset_counts()
            db.session.remove()
            db.drop_all()
    os.environ.pop("DATABASE_URL", None)


@pytest.fixture
def limited_app(tmp_path, monkeypatch):
    yield from _limited_app(tmp_path, monkeypatch, "memory://")


@pytest.fixture
def app_with_limiter_store_down(tmp_path, monkeypatch):
    yield from _limited_app(tmp_path, monkeypatch, "redis://127.0.0.1:1/0")  # nothing listens there


def _login(client, email, ip="203.0.113.7"):
    return client.post(
        "/login", data={"email": email, "password": "wrong-password"},
        environ_base={"REMOTE_ADDR": ip},
    )


def test_guessing_one_account_is_stopped(limited_app):
    make_member(email="target@example.com")
    client = limited_app.test_client()

    statuses = [_login(client, "target@example.com").status_code for _ in range(11)]

    assert 429 not in statuses[:10]
    assert statuses[10] == 429


def test_a_lecture_hall_on_one_address_can_still_log_in(limited_app):
    """Forty different students behind one campus address, one attempt each."""
    client = limited_app.test_client()

    statuses = [_login(client, f"student{n}@example.com").status_code for n in range(40)]

    assert 429 not in statuses


def test_one_blocked_account_does_not_block_the_next_student(limited_app):
    client = limited_app.test_client()
    for _ in range(11):
        _login(client, "unlucky@example.com")

    assert _login(client, "someone.else@example.com").status_code != 429


def test_the_same_account_from_another_network_is_counted_separately(limited_app):
    client = limited_app.test_client()
    for _ in range(11):
        _login(client, "roaming@example.com", ip="203.0.113.7")

    assert _login(client, "roaming@example.com", ip="198.51.100.9").status_code != 429


def test_many_signups_from_one_address_are_let_through(limited_app, monkeypatch):
    """The signup form's own limit is per address too; 30 students is fine."""
    client = limited_app.test_client()

    statuses = [
        client.post("/process-membership", data={"email_private": f"new{n}@example.com"},
                    environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code
        for n in range(30)
    ]

    assert 429 not in statuses


def test_signed_in_limits_count_per_account(limited_app):
    """Resending a confirmation from a shared campus address is counted per person."""
    first = make_member(email="first@example.com")
    second = make_member(email="second@example.com")

    def resend_as(member):
        from flask import g

        # The fixture holds one app context open across requests, and
        # Flask-Login caches the loaded user on it; a real request starts
        # clean.
        g.pop("_login_user", None)
        client = limited_app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        return [
            client.post("/api/v1/account/emails/private/confirmation",
                        environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code
            for _ in range(10)
        ]

    assert 429 not in resend_as(first)
    assert 429 not in resend_as(second)


def test_logging_in_keeps_working_when_the_limiter_store_is_down(app_with_limiter_store_down):
    """Redis unreachable: the limits fall back to memory instead of failing the request."""
    client = app_with_limiter_store_down.test_client()

    statuses = [_login(client, "target@example.com").status_code for _ in range(11)]

    assert 500 not in statuses
    assert statuses[0] == 200     # the form again, "invalid email or password"
    assert statuses[10] == 429    # and still limited, in memory


def test_the_portal_limiter_falls_back_to_memory():
    limiter = app_module.limiter

    assert limiter._in_memory_fallback_enabled is True
    assert limiter._swallow_errors is True


@pytest.mark.parametrize("setting", [
    "RATELIMIT_LOGIN_PER_IP", "RATELIMIT_REGISTER_PER_IP", "RATELIMIT_MEMBERSHIP_PER_IP",
])
def test_a_whole_campus_behind_one_address_fits_under_the_network_limits(setting):
    """250 students on the university Wi-Fi, several tries each, one address."""
    from limits import parse

    from aeronautics_members import config

    assert parse(getattr(config, setting)).amount >= 1000


def test_going_to_the_forum_in_a_loop_is_stopped_per_account(limited_app):
    """Every click through to the forum makes the forum do work, and retries
    what is waiting for that person at once. A loop must not become a flood;
    a second person on the same campus address is not held up by it."""
    from flask import g

    def clicks_as(member, times):
        g.pop("_login_user", None)
        client = limited_app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        return [
            client.get("/forum/discourse/connect", environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code
            for _ in range(times)
        ]

    looping = make_member(email="looping@example.com")
    other = make_member(email="other@example.com")

    statuses = clicks_as(looping, 21)
    assert 429 not in statuses[:20]
    assert statuses[20] == 429
    assert 429 not in clicks_as(other, 3)
