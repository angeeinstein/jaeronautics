"""The footer on every page, and where its links lead."""
import pytest

from conftest import make_member
from aeronautics_members import config


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


@pytest.mark.parametrize("path", ["/", "/login", "/legal", "/forgot-password"])
def test_every_public_page_has_it(client, path):
    body = client.get(path).get_data(as_text=True)

    assert '<footer class="site-footer">' in body
    assert "mailto:office@joanneum-aeronautics.at" in body
    assert "Impressum" in body
    assert "Privacy" in body
    assert ">Statutes</a>" in body
    assert "&copy; 20" in body or "© 20" in body


def test_signed_in_pages_have_it_too(client, app):
    member = make_member(email="footer@example.com")
    _login(client, member.user.id)

    # The app's pages draw it from here (frontend/src/frame/Footer.tsx).
    site = client.get("/api/v1/site").get_json()

    assert [link["label"] for link in site["footer"]] == [
        "Impressum", "Privacy", "Statutes", "Legal texts", "Contact", "Website"]
    assert site["copyright"].startswith("© 20")


def test_until_configured_the_links_lead_to_the_portals_own_legal_texts(client):
    body = client.get("/login").get_data(as_text=True)
    footer = body.split('<footer class="site-footer">')[1]

    for path in ("/legal/legal-notice", "/legal/privacy-policy", "/legal/statutes", "/legal"):
        assert f'href="{path}"' in footer
    assert footer.count('href="https://joanneum-aeronautics.at"') == 1  # the website

    for path in ("/legal/legal-notice", "/legal/privacy-policy", "/legal/statutes"):
        assert client.get(path).status_code == 200, path


def test_configured_addresses_are_used(client, monkeypatch):
    monkeypatch.setattr(config, "IMPRESSUM_URL", "https://example.org/impressum")
    monkeypatch.setattr(config, "PRIVACY_URL", "https://example.org/privacy")
    monkeypatch.setattr(config, "STATUTES_URL", "https://example.org/statutes")

    footer = client.get("/login").get_data(as_text=True).split('<footer class="site-footer">')[1]

    assert 'href="https://example.org/impressum"' in footer
    assert 'href="https://example.org/privacy"' in footer
    assert 'href="https://example.org/statutes"' in footer
    assert 'href="/legal/statutes"' not in footer and 'href="/legal/legal-notice"' not in footer
