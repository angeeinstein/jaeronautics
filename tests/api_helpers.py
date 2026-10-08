"""Calling the JSON API from tests, the way the front end does.

``signed_in(client, user)`` puts a session in place; ``send(client, method,
path, json)`` sends a change with the CSRF token from ``/api/v1/session`` in
``X-CSRFToken`` -- needed only where a test switches CSRF checks on, and sent
always so the tests read like the front end's calls.
"""


def signed_in(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
    return client


def csrf_token(client):
    return client.get("/api/v1/session").get_json()["csrf_token"]


def send(client, method, path, json=None):
    return client.open(path, method=method, json=json, headers={"X-CSRFToken": csrf_token(client)})


def said(client):
    """What Flask flashed before sending the browser on, as an app page shows it
    (GET /api/v1/messages): the texts, joined."""
    return " ".join(message["text"] for message in client.get("/api/v1/messages").get_json()["messages"])
