from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.middleware.mtls import MTLSMiddleware

app = FastAPI()
app.add_middleware(MTLSMiddleware)


@app.get("/")
def root():
    return {"message": "Hello World"}


@app.get("/secure")
def secure():
    return {"message": "Secure Data"}


client = TestClient(app)


def test_mtls_disabled_by_default(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    response = client.get("/secure")
    assert response.status_code == 200


def test_mtls_enabled_no_header(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    response = client.get("/secure")
    assert response.status_code == 403
    assert response.json() == {"detail": "Client Certificate Required"}


def test_mtls_enabled_with_header(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    response = client.get("/secure", headers={"X-ARR-ClientCert": "MIID..."})
    assert response.status_code == 200


def test_mtls_public_paths(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    # / is in public_paths
    response = client.get("/")
    assert response.status_code == 200
