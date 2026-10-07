"""The JSON API's plumbing: signing in, permissions, input, answers, errors.

Tried on endpoints of the tests' own (a blueprint of their own under
/api/v1/_test), so every rule is shown on the smallest endpoint that has it;
then /session and /me, the front end's first two calls, and the OpenAPI
description built from the declarations.
"""
from datetime import date, datetime

import pytest
from flask import Blueprint

from conftest import app_module, make_member
from aeronautics_members.api._core import Api, Model, UtcDateTime
from aeronautics_members.api import openapi
from aeronautics_members.permissions import Permission
from aeronautics_members.services import ConflictError
from api_helpers import send, signed_in
from test_admin_reviews import _picture, _staff

trial_bp = Blueprint("api_trial", __name__, url_prefix="/api/v1/_test")
trial = Api(trial_bp)


class EchoIn(Model):
    name: str
    count: int = 1


class EchoOut(Model):
    text: str


class ListQuery(Model):
    page: int = 1
    search: str | None = None


class WhenOut(Model):
    at: UtcDateTime
    day: date


@trial.endpoint("POST", "/echo", body=EchoIn, response=EchoOut, status=201)
def echo(body):
    """Repeat the name."""
    return EchoOut(text=" ".join([body.name] * body.count))


@trial.endpoint("GET", "/list", query=ListQuery, response=EchoOut, public=True)
def listing(query):
    return EchoOut(text=f"page {query.page} {query.search or ''}".strip())


@trial.endpoint("POST", "/things/<int:thing_id>", response=None)
def conflict(thing_id):
    raise ConflictError(f"Thing {thing_id} is taken.", code="thing_taken", details={"fields": {"name": "Taken."}})


@trial.endpoint("GET", "/admin", response=EchoOut, permissions=[Permission.ACCOUNTS_VIEW])
def admin_only():
    return EchoOut(text="secret")


@trial.endpoint("GET", "/when", response=WhenOut, public=True)
def when():
    return WhenOut(at=datetime(2026, 10, 6, 7, 38, 11), day=date(2026, 10, 6))


@trial.endpoint("POST", "/upload", uploads={"main": True, "extra": False}, produces="text/plain")
def upload(files):
    """Answer the files' names, as a file."""
    from flask import Response

    names = [files["main"].filename, files["extra"].filename if files["extra"] else "-"]
    return Response(" ".join(names), mimetype="text/plain")


@pytest.fixture
def api(app):
    app.register_blueprint(trial_bp)
    return app.test_client()


def _error(response):
    return response.status_code, response.get_json()["error"]["code"]


class TestSigningIn:
    def test_signed_out_is_401_as_json_not_a_redirect(self, api):
        response = api.post("/api/v1/_test/echo", json={"name": "x"})

        assert _error(response) == (401, "not_signed_in")

    def test_public_needs_nobody(self, api):
        assert api.get("/api/v1/_test/list").get_json() == {"text": "page 1"}

    def test_without_the_permission_403(self, app, api):
        signed_in(api, make_member().user)

        assert _error(api.get("/api/v1/_test/admin")) == (403, "forbidden")

    def test_with_it(self, app, api):
        signed_in(api, _staff("boss@example.org", "admin"))

        assert api.get("/api/v1/_test/admin").get_json() == {"text": "secret"}


class TestInput:
    def test_a_body_is_read_into_its_model(self, app, api):
        signed_in(api, make_member().user)

        response = send(api, "POST", "/api/v1/_test/echo", {"name": "ja", "count": 2})

        assert response.status_code == 201 and response.get_json() == {"text": "ja ja"}

    @pytest.mark.parametrize("body, field", [
        ({}, "name"), ({"name": "x", "count": "many"}, "count"), ({"name": "x", "colour": "red"}, "colour"),
    ])
    def test_what_does_not_fit_is_400_per_field(self, app, api, body, field):
        signed_in(api, make_member().user)

        response = send(api, "POST", "/api/v1/_test/echo", body)

        assert _error(response) == (400, "validation_error")
        assert field in response.get_json()["error"]["fields"]

    def test_no_json_at_all(self, app, api):
        signed_in(api, make_member().user)

        assert _error(api.post("/api/v1/_test/echo", data="name=x")) == (400, "validation_error")

    def test_the_query_string(self, api):
        assert api.get("/api/v1/_test/list?page=3&search=rocket").get_json() == {"text": "page 3 rocket"}
        assert _error(api.get("/api/v1/_test/list?page=first")) == (400, "validation_error")


class TestAnswers:
    def test_a_service_error_with_its_code_and_fields(self, app, api):
        signed_in(api, make_member().user)

        body = send(api, "POST", "/api/v1/_test/things/7").get_json()

        assert body == {"error": {"code": "thing_taken", "message": "Thing 7 is taken.",
                                  "fields": {"name": "Taken."}, "details": None}}

    def test_times_in_utc_days_as_days(self, api):
        assert api.get("/api/v1/_test/when").get_json() == {"at": "2026-10-06T07:38:11Z", "day": "2026-10-06"}

    def test_never_cached(self, api):
        assert "no-store" in api.get("/api/v1/_test/list").headers["Cache-Control"]

    def test_unknown_address_and_wrong_method_as_json(self, api):
        assert _error(api.get("/api/v1/nothing-here")) == (404, "not_found")
        assert _error(api.delete("/api/v1/_test/list")) == (405, "method_not_allowed")

    def test_the_pages_keep_their_html_404(self, client):
        response = client.get("/nothing-here")

        assert response.status_code == 404 and response.mimetype == "text/html"


class TestCsrf:
    def test_a_change_without_the_token_is_refused(self, app, api):
        app.config["WTF_CSRF_ENABLED"] = True
        signed_in(api, make_member().user)

        assert _error(api.post("/api/v1/_test/echo", json={"name": "x"})) == (400, "csrf_failed")

    def test_with_the_token_from_the_session(self, app, api):
        app.config["WTF_CSRF_ENABLED"] = True
        signed_in(api, make_member().user)

        assert send(api, "POST", "/api/v1/_test/echo", {"name": "x"}).status_code == 201


class TestSession:
    def test_signed_out(self, client):
        body = client.get("/api/v1/session").get_json()

        assert body["signed_in"] is False and body["csrf_token"]

    def test_signed_in(self, app, client):
        signed_in(client, make_member().user)

        assert client.get("/api/v1/session").get_json()["signed_in"] is True


class TestMe:
    def test_signed_out(self, client):
        assert _error(client.get("/api/v1/me")) == (401, "not_signed_in")

    def test_a_member(self, app, client):
        member = make_member(first_name="Anna", last_name="Berger")
        signed_in(client, member.user)

        me = client.get("/api/v1/me").get_json()

        assert (me["first_name"], me["last_name"], me["email"]) == ("Anna", "Berger", "member@example.com")
        assert me["roles"] == [] and me["admin_area"] is False and me["counts"] == {"reviews_waiting": 0}
        assert me["forum_area"] is True and me["picture_url"] is None

    def test_their_picture_once_it_is_approved(self, app, client):
        member = make_member()
        waiting = _picture(member, token="waiting")
        signed_in(client, member.user)
        assert client.get("/api/v1/me").get_json()["picture_url"] is None

        waiting.status = "approved"
        app_module.db.session.commit()

        assert client.get("/api/v1/me").get_json()["picture_url"] == "/forum/avatar/public/waiting"

    def test_an_admin_without_a_membership(self, app, client):
        signed_in(client, _staff("boss@example.org", "admin"))

        me = client.get("/api/v1/me").get_json()

        assert me["first_name"] is None and me["roles"] == ["admin"] and me["admin_area"] is True
        assert me["forum_area"] is False  # the forum is for members
        assert Permission.ACCOUNTS_VIEW in me["permissions"] and me["permissions"] == sorted(me["permissions"])

    def test_an_erased_account_is_signed_out(self, app, client):
        member = make_member()
        member.user.deleted_at = datetime(2026, 1, 1)
        app_module.db.session.commit()
        signed_in(client, member.user)

        assert _error(client.get("/api/v1/me")) == (401, "not_signed_in")


class TestFiles:
    def test_the_files_reach_the_function_and_a_file_comes_back(self, api, app):
        import io

        signed_in(api, _staff("boss@example.org", "admin"))

        response = api.post("/api/v1/_test/upload", data={"main": (io.BytesIO(b"x"), "a.md")},
                            content_type="multipart/form-data", headers={"X-CSRFToken": "x"})

        assert response.status_code == 200 and response.get_data(as_text=True) == "a.md -"

    def test_a_required_file_missing_is_said_at_its_field(self, api, app):
        signed_in(api, _staff("boss@example.org", "admin"))

        response = api.post("/api/v1/_test/upload", data={}, content_type="multipart/form-data")

        assert _error(response) == (400, "validation_error")
        assert response.get_json()["error"]["fields"] == {"main": "Choose a file."}

    def test_described_as_a_form_with_files_and_a_file_answer(self, app):
        operation = openapi.build()["paths"]["/api/v1/admin/legal/preview"]["post"]

        form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
        assert form["required"] == ["german"] and form["properties"]["english"]["format"] == "binary"
        assert operation["responses"]["200"]["content"] == {
            "application/pdf": {"schema": {"type": "string", "format": "binary"}}}
        assert "400" in operation["responses"]


class TestTheDescription:
    def test_every_endpoint_described(self, app):
        document = openapi.build()

        assert document["openapi"] == "3.1.0"
        assert set(document["paths"]) >= {"/api/v1/session", "/api/v1/me"}
        me = document["paths"]["/api/v1/me"]["get"]
        assert me["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/MeOut"}
        assert set(me["responses"]) == {"200", "401"}
        assert {"MeOut", "SessionOut", "Counts", "ErrorOut"} <= set(document["components"]["schemas"])

    def test_every_endpoint_says_what_it_answers(self, app):
        from aeronautics_members.api import ENDPOINTS

        assert [e.name for e in ENDPOINTS if e.response is None and e.method == "GET"] == []
        assert [e.name for e in ENDPOINTS if not e.summary] == []

    def test_every_model_has_a_name_of_its_own(self, app):
        """Two models of one name come out as "aeronautics_members__api__..__Person":
        the front end's types would carry the module path."""
        names = openapi.build()["components"]["schemas"]

        assert [name for name in names if "__" in name] == []

    def test_the_command(self, app, tmp_path):
        result = app.test_cli_runner().invoke(args=["api-schema", "--out", str(tmp_path / "openapi.json")])

        assert result.exit_code == 0 and '"/api/v1/me"' in (tmp_path / "openapi.json").read_text()
