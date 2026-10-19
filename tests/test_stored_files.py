"""A stored picture is served from inside its own folder, never from a path a
restored backup made up (services/stored_files.py)."""
from conftest import db
from aeronautics_members.db_models import ImportedForumProfile
from aeronautics_members.forum_service import get_forum_storage_dir
from aeronautics_members.services.stored_files import stored_file
from test_teams_flow import _person


def test_a_file_inside_its_folder_and_nothing_else(tmp_path):
    folder = tmp_path / "pictures"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"jpeg")
    (tmp_path / ".env").write_text("SECRET_KEY=x")

    assert stored_file(str(folder / "a.jpg"), folder) == (folder / "a.jpg").resolve()
    assert stored_file(str(tmp_path / ".env"), folder) is None
    assert stored_file(str(folder / ".." / ".env"), folder) is None
    assert stored_file(str(folder / "gone.jpg"), folder) is None
    assert stored_file(None, folder) is None


def test_a_public_avatar_pointing_out_of_its_folder_is_not_found(app, client, tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("SECRET_KEY=x")
    anna = _person("anna@example.com")
    db.session.add(ImportedForumProfile(user_id=anna.id, source_user_id=7, source_username="anna", display_name="Anna",
                                        avatar_path=str(secret), avatar_public_token="tok"))
    db.session.commit()
    assert not str(secret.resolve()).startswith(str(get_forum_storage_dir().resolve()))

    response = client.get("/forum/avatar/imported/tok")

    assert response.status_code == 404 and b"SECRET_KEY" not in response.data


def test_a_host_the_browser_names_itself_is_not_believed(app):
    """nginx sets no X-Forwarded-Host, so one arriving was sent by the browser."""
    from werkzeug.middleware.proxy_fix import ProxyFix

    layer = app.wsgi_app
    while not isinstance(layer, ProxyFix):
        layer = layer.app
    environ = {"HTTP_HOST": "members.test", "HTTP_X_FORWARDED_HOST": "evil.example", "REMOTE_ADDR": "127.0.0.1",
               "wsgi.url_scheme": "http", "REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    seen = {}
    ProxyFix(lambda env, start: seen.update(host=env["HTTP_HOST"]) or [], x_for=layer.x_for,
             x_host=layer.x_host, x_proto=layer.x_proto)(environ, None)

    assert layer.x_host == 0 and seen["host"] == "members.test"
