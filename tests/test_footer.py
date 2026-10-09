"""The footer on every page, and where its links lead."""
from conftest import db, make_member


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


def test_every_page_has_it(client):
    """The app draws it from here on every page, signed in or not (frontend/src/frame/Footer.tsx)."""
    site = client.get("/api/v1/site").get_json()

    assert [link["label"] for link in site["footer"]] == [
        "Impressum", "Privacy", "Statutes", "Legal texts", "Contact", "Website"]
    assert {"label": "Contact", "url": "/contact", "external": False} in site["footer"]
    assert site["copyright"].startswith("© 20")


def test_signed_in_too(client, app):
    member = make_member(email="footer@example.com")
    _login(client, member.user.id)

    assert client.get("/api/v1/site").status_code == 200


def _links(client):
    return {link["label"]: link for link in client.get("/api/v1/site").get_json()["footer"]}


def test_until_configured_the_links_lead_to_the_portals_own_legal_texts(client):
    links = _links(client)

    assert [links[label]["url"] for label in ("Impressum", "Privacy", "Statutes", "Legal texts")] == [
        "/legal/legal-notice", "/legal/privacy-policy", "/legal/statutes", "/legal"]
    assert links["Website"] == {"label": "Website", "url": "https://joanneum-aeronautics.at", "external": True}

    for path in ("/legal/legal-notice", "/legal/privacy-policy", "/legal/statutes"):
        assert client.get(path).status_code == 200, path


def test_configured_addresses_are_used(client, monkeypatch):
    from aeronautics_members.api import site

    monkeypatch.setattr(site, "IMPRESSUM_URL", "https://example.org/impressum")
    monkeypatch.setattr(site, "PRIVACY_URL", "https://example.org/privacy")
    monkeypatch.setattr(site, "STATUTES_URL", "https://example.org/statutes")

    links = _links(client)

    assert links["Impressum"] == {"label": "Impressum", "url": "https://example.org/impressum", "external": True}
    assert links["Privacy"]["url"] == "https://example.org/privacy"
    assert links["Statutes"]["url"] == "https://example.org/statutes"


def test_what_teams_are_called_once_they_are_switched_on(client, app):
    from aeronautics_members.services import teams

    assert client.get("/api/v1/site").get_json()["teams_label"] is None
    teams.save_team_settings(None, enabled=True, label_singular="Project", label_plural="Projects")
    db.session.commit()
    assert client.get("/api/v1/site").get_json()["teams_label"] == "Projects"
