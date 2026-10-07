"""What a browser is shown when something goes wrong (app.py's error handlers).

An address that is no page: the app, which says so in its own frame. Too many
requests, an error on our side, a form that could not be sent: the plain error
page (templates/error.html), which must work even when the database is what
failed. The API answers all of these as JSON.
"""
import pytest
from flask import abort

from conftest import db, make_member
from test_rate_limits import limited_app  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def failing_routes(app):
    app.add_url_rule("/__test-500", "test_500", lambda: abort(500))
    app.add_url_rule("/__test-413", "test_413", lambda: abort(413))


def test_no_such_page_is_the_apps_with_404(client):
    response = client.get("/no-such-page")

    assert response.status_code == 404
    assert '<div id="root"></div>' in response.get_data(as_text=True)


def test_the_api_answers_json(client):
    response = client.get("/api/v1/no-such-thing")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_an_error_on_our_side(client):
    response = client.get("/__test-500")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "<h1>Something went wrong</h1>" in body and 'href="/"' in body
    assert "/static/maintenance.css" in body


def test_the_error_page_needs_nothing_from_the_site(app, client):
    """Rendered without the context processors: with the database down, they
    would fail too, and the error page with them."""
    @app.context_processor
    def broken():
        raise RuntimeError("the database is down")

    response = client.get("/__test-500")

    assert response.status_code == 500 and "Something went wrong" in response.get_data(as_text=True)
    db.session.rollback()


def test_too_large(client):
    response = client.get("/__test-413")

    assert response.status_code == 413 and "Too large" in response.get_data(as_text=True)


def test_too_many_requests_says_what_to_do(limited_app):  # noqa: F811
    """A page, not a redirect: browsers do not follow one on a 429."""
    from flask import g

    member = make_member(email="exporter@example.com")
    client = limited_app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(member.user.id)
    g.pop("_login_user", None)

    responses = [client.get("/account/data-export") for _ in range(11)]

    assert [r.status_code for r in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429
    text = responses[10].get_data(as_text=True)
    # What happened, what to do -- and the likely case, a shared network.
    assert "Too many attempts" in text and "wait a few minutes" in text and "WiFi" in text
