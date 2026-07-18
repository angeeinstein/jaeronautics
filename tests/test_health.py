"""Tests for the /__health endpoint's database probe."""
import json

from conftest import db
import aeronautics_members.blueprints.public as public_module


def test_health_ok_when_db_reachable(client):
    resp = client.get("/__health")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    assert payload["status"] == "ok"
    assert payload["checks"]["database"] == "ok"


def test_health_degraded_when_db_unreachable(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(public_module.db.session, "execute", boom)
    resp = client.get("/__health")
    assert resp.status_code == 503
    payload = json.loads(resp.data)
    assert payload["status"] == "degraded"
    assert payload["checks"]["database"] == "error"
