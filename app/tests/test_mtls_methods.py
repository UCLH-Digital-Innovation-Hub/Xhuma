from fastapi.testclient import TestClient
from app.main import app
import os

client = TestClient(app)


def test_public_path_allows_get():
    # Health check is public and allows GET
    response = client.get("/health")
    assert response.status_code == 200


def test_public_path_rejects_post():
    # Public paths should reject POST if REQUIRE_MTLS is true
    os.environ["REQUIRE_MTLS"] = "true"
    response = client.post("/health", json={"data": "test"})
    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}


def test_public_path_rejects_put():
    os.environ["REQUIRE_MTLS"] = "true"
    response = client.put("/health", json={"data": "test"})
    assert response.status_code == 405
