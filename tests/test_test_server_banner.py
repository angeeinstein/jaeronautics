"""TEST_SERVER in .env: every page and every email says it is the test server."""
import pytest

from aeronautics_members import mail_utils
from test_mail_sender import _account, smtp  # noqa: F401  (fixture)


@pytest.fixture
def test_server(app):
    app.config["TEST_SERVER"] = True
    yield
    app.config["TEST_SERVER"] = False


def _send_test_email():
    with mail_utils.current_app.test_request_context():
        return mail_utils.send_mail("brevo", "anna@example.com", "Test email", template_name="test_email.html",
                                    return_error=True)


def _html(message):
    for part in message.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode()
    raise AssertionError("no HTML part")


@pytest.mark.parametrize("path", ["/", "/no-such-page"])
def test_the_live_site_shows_no_bar(client, path):
    page = client.get(path).get_data(as_text=True)

    assert "test-server-bar" not in page and "data-test-server" not in page
    assert "[TEST]" not in page


def test_an_app_page_on_the_test_server_says_so(app, client, test_server, tmp_path):
    """The app draws the bar where the page is marked (frontend/src/frame/TestServerBar.tsx)."""
    (tmp_path / "index.html").write_text('<!doctype html><html lang="en"><head><title>Joanneum Aeronautics</title>'
                                         '</head><body><div id="root"></div></body></html>')
    app.config["FRONTEND_DIST_DIR"] = str(tmp_path)

    page = client.get("/").get_data(as_text=True)

    assert '<html data-test-server lang="en">' in page
    assert "<title>[TEST] Joanneum Aeronautics</title>" in page


def test_a_flask_page_on_the_test_server_says_so(client, test_server):
    page = client.get("/no-such-page").get_data(as_text=True)

    assert 'class="test-server-bar"' in page
    assert "<title>[TEST] " in page


def test_an_email_from_the_test_server_says_so(app, smtp, test_server):  # noqa: F811
    _account()

    assert _send_test_email() == (True, None)

    message = smtp.sent[-1][2]
    assert message["Subject"] == "[TEST] Test email"
    assert "TEST SERVER" in _html(message)


def test_an_email_from_the_live_site_has_no_bar(app, smtp):  # noqa: F811
    _account()

    _send_test_email()

    message = smtp.sent[-1][2]
    assert message["Subject"] == "Test email"
    assert "TEST SERVER" not in _html(message)
