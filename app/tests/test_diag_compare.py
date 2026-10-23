"""Tests for the FHIR-vs-XML comparison diagnostics (Option A).

Covers the gating helpers in ``app.settings`` and the ``/_diag/compare`` viewer. The viewer is
registered at import time based on the environment, so these tests reload ``app.main`` after
setting ``ENV`` -- the same pattern used by ``test_main_security.py`` for ``/_dev/audit``. The
``TestClient`` is used without a ``with`` block so the lifespan/startup (Redis + Postgres) never
runs; the handler only touches ``redis_client``, which is patched with a tiny in-memory fake.
"""

import importlib

import pytest

from app.settings import diag_compare_enabled, diag_ttl_minutes

# --------------------------------------------------------------------------- #
# Gate + TTL helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("env", ["dev", "local", "int", "DEV", "Int"])
def test_gate_on_in_nonprod(monkeypatch, env):
    monkeypatch.setenv("ENV", env)
    monkeypatch.delenv("DIAG_COMPARE_ENABLED", raising=False)
    assert diag_compare_enabled() is True


def test_gate_off_in_prod_by_default(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.delenv("DIAG_COMPARE_ENABLED", raising=False)
    assert diag_compare_enabled() is False


def test_gate_defaults_to_prod_off(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("DIAG_COMPARE_ENABLED", raising=False)
    assert diag_compare_enabled() is False


@pytest.mark.parametrize("flag", ["1", "true", "YES", "on"])
def test_gate_prod_opt_in_truthy(monkeypatch, flag):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("DIAG_COMPARE_ENABLED", flag)
    assert diag_compare_enabled() is True


@pytest.mark.parametrize("flag", ["0", "false", "no", "", "off"])
def test_gate_prod_opt_in_falsey(monkeypatch, flag):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("DIAG_COMPARE_ENABLED", flag)
    assert diag_compare_enabled() is False


def test_ttl_default(monkeypatch):
    monkeypatch.delenv("DIAG_TTL_MIN", raising=False)
    assert diag_ttl_minutes() == 30


@pytest.mark.parametrize("val,expected", [("5", 5), ("120", 120)])
def test_ttl_valid(monkeypatch, val, expected):
    monkeypatch.setenv("DIAG_TTL_MIN", val)
    assert diag_ttl_minutes() == expected


@pytest.mark.parametrize("val", ["0", "-5", "abc", ""])
def test_ttl_invalid_falls_back(monkeypatch, val):
    monkeypatch.setenv("DIAG_TTL_MIN", val)
    assert diag_ttl_minutes() == 30


# --------------------------------------------------------------------------- #
# Viewer: registration + rendering
# --------------------------------------------------------------------------- #


class _FakeRedis:
    """Minimal async stand-in exposing just the ``get`` used by the viewer."""

    def __init__(self, store):
        self._store = store

    async def get(self, key):
        return self._store.get(str(key))


def _reload_main(monkeypatch, env, flag=None):
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    monkeypatch.setenv("API_KEY", "TEST_KEY")
    if flag is None:
        monkeypatch.delenv("DIAG_COMPARE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("DIAG_COMPARE_ENABLED", flag)
    import app.main

    importlib.reload(app.main)
    return app.main


def _paths(main):
    return {getattr(r, "path", None) for r in main.app.routes}


def test_route_absent_in_prod(monkeypatch):
    main = _reload_main(monkeypatch, "prod")
    assert "/_diag/compare" not in _paths(main)


def test_route_present_in_int(monkeypatch):
    main = _reload_main(monkeypatch, "int")
    assert "/_diag/compare" in _paths(main)


def test_route_present_in_prod_with_flag(monkeypatch):
    main = _reload_main(monkeypatch, "prod", flag="true")
    assert "/_diag/compare" in _paths(main)


def test_requires_api_key(monkeypatch):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    client = TestClient(main.app)
    resp = client.get("/_diag/compare")
    assert resp.status_code == 401


def test_split_view_renders_both(monkeypatch):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    store = {
        "9690937278": b"doc-123",
        "diag:bundle:doc-123": b'{"resourceType": "Bundle", "id": "abc"}',
        "diag:xml:doc-123": b"<ClinicalDocument>hello</ClinicalDocument>",
    }
    monkeypatch.setattr(main, "redis_client", _FakeRedis(store))
    client = TestClient(main.app)
    resp = client.get("/_diag/compare", params={"nhsno": "9690937278"}, headers={"X-API-Key": "TEST_KEY"})
    assert resp.status_code == 200
    assert "resourceType" in resp.text
    assert "ClinicalDocument" in resp.text
    assert "Side-by-side" in resp.text


@pytest.mark.parametrize("view", ["split", "tabs", "diff"])
def test_doc_uuid_direct_all_views(monkeypatch, view):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    store = {
        "diag:bundle:doc-9": b'{"resourceType": "Bundle"}',
        "diag:xml:doc-9": b"<ClinicalDocument/>",
    }
    monkeypatch.setattr(main, "redis_client", _FakeRedis(store))
    client = TestClient(main.app)
    resp = client.get(
        "/_diag/compare",
        params={"doc_uuid": "doc-9", "view": view},
        headers={"X-API-Key": "TEST_KEY"},
    )
    assert resp.status_code == 200
    assert "ClinicalDocument" in resp.text


def test_invalid_nhs_number(monkeypatch):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    monkeypatch.setattr(main, "redis_client", _FakeRedis({}))
    client = TestClient(main.app)
    resp = client.get("/_diag/compare", params={"nhsno": "not-a-number"}, headers={"X-API-Key": "TEST_KEY"})
    assert resp.status_code == 200
    assert "Invalid NHS number" in resp.text


def test_missing_diag_returns_message(monkeypatch):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    monkeypatch.setattr(main, "redis_client", _FakeRedis({}))
    client = TestClient(main.app)
    resp = client.get("/_diag/compare", params={"doc_uuid": "nope"}, headers={"X-API-Key": "TEST_KEY"})
    assert resp.status_code == 200
    assert "expired or not captured" in resp.text


def test_form_when_no_params(monkeypatch):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    monkeypatch.setattr(main, "redis_client", _FakeRedis({}))
    client = TestClient(main.app)
    resp = client.get("/_diag/compare", headers={"X-API-Key": "TEST_KEY"})
    assert resp.status_code == 200
    assert "<form" in resp.text


def test_unknown_view_falls_back_to_split(monkeypatch):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    store = {
        "diag:bundle:doc-5": b'{"resourceType": "Bundle"}',
        "diag:xml:doc-5": b"<ClinicalDocument/>",
    }
    monkeypatch.setattr(main, "redis_client", _FakeRedis(store))
    client = TestClient(main.app)
    resp = client.get(
        "/_diag/compare",
        params={"doc_uuid": "doc-5", "view": "bogus"},
        headers={"X-API-Key": "TEST_KEY"},
    )
    assert resp.status_code == 200
    assert "Side-by-side" in resp.text


def test_one_sided_capture_shows_fallback(monkeypatch):
    from fastapi.testclient import TestClient

    main = _reload_main(monkeypatch, "int")
    # Only the XML survived (e.g. the bundle key expired first); the page still renders.
    store = {"diag:xml:doc-7": b"<ClinicalDocument/>"}
    monkeypatch.setattr(main, "redis_client", _FakeRedis(store))
    client = TestClient(main.app)
    resp = client.get("/_diag/compare", params={"doc_uuid": "doc-7"}, headers={"X-API-Key": "TEST_KEY"})
    assert resp.status_code == 200
    assert "FHIR bundle not captured" in resp.text
    assert "ClinicalDocument" in resp.text
