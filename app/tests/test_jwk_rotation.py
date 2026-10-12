from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jwcrypto import jwk

from app.main import get_jwk
from app.pds.pds import lookup_patient
from app.security import append_public_jwk, pds_jwt
from app.tests.fixtures.saml_attributes import saml


@pytest.fixture
def signing_keys():
    return [jwk.JWK.generate(kty="RSA", size=2048) for _ in range(3)]


@pytest.fixture
def jwk_client():
    app = FastAPI()
    app.state.jwk_json = {}
    app.state.jwk_keys = []
    app.add_api_route("/jwk", get_jwk)
    with TestClient(app) as client:
        yield client


def test_rotation_retains_only_current_and_previous_keys(jwk_client, signing_keys, monkeypatch):
    old_key, new_key, third_key = signing_keys
    append_public_jwk(jwk_client.app, old_key.export_to_pem(private_key=True, password=None))
    for previous_key, key in zip(signing_keys, signing_keys[1:]):
        old_token = jwt.encode(
            {"sub": "rotation-test"}, previous_key.export_to_pem(private_key=True, password=None), "RS512"
        )
        # Environment secrets can contain escaped newlines.
        pem = key.export_to_pem(private_key=True, password=None).decode().replace("\n", "\\n")
        monkeypatch.setenv("JWTKEY", pem)
        response = jwk_client.get("/jwk")
        assert response.status_code == 200
        public_keys = response.json()["keys"]
        assert [key["kid"] for key in public_keys] == [previous_key.thumbprint(), key.thumbprint()]
        assert jwk_client.app.state.jwk_json["kid"] == key.thumbprint()
        assert jwk_client.get("/jwk").json()["keys"] == public_keys
        public_pem = jwk.JWK(**public_keys[0]).export_to_pem()
        assert jwt.decode(old_token, public_pem, algorithms=["RS512"])["sub"] == "rotation-test"
    for key in public_keys:
        assert key["alg"] == "RS512"
        assert key["use"] == "sig"
        assert not ({"d", "p", "q", "dp", "dq", "qi", "oth"} & key.keys())
    # Reusing the previous key makes the former current key the previous one.
    monkeypatch.setenv("JWTKEY", new_key.export_to_pem(private_key=True, password=None).decode())
    public_keys = jwk_client.get("/jwk").json()["keys"]
    assert [key["kid"] for key in public_keys] == [third_key.thumbprint(), new_key.thumbprint()]
    assert jwk_client.get("/jwk").json()["keys"] == public_keys
    assert jwk_client.app.state.jwk_json["kid"] == new_key.thumbprint()

    # Reintroducing an evicted key still retains only the last two distinct keys.
    monkeypatch.setenv("JWTKEY", old_key.export_to_pem(private_key=True, password=None).decode())
    public_keys = jwk_client.get("/jwk").json()["keys"]
    assert [key["kid"] for key in public_keys] == [new_key.thumbprint(), old_key.thumbprint()]
    assert jwk_client.get("/jwk").json()["keys"] == public_keys
    assert jwk_client.app.state.jwk_json["kid"] == old_key.thumbprint()


@pytest.mark.parametrize("jwt_key", [None, "invalid PEM"])
def test_missing_or_invalid_key_preserves_existing_chain(jwk_client, signing_keys, monkeypatch, jwt_key):
    append_public_jwk(jwk_client.app, signing_keys[0].export_to_pem(private_key=True, password=None))
    existing_keys = list(jwk_client.app.state.jwk_keys)
    if jwt_key is None:
        monkeypatch.delenv("JWTKEY")
    else:
        monkeypatch.setenv("JWTKEY", jwt_key)

    response = jwk_client.get("/jwk")
    assert response.status_code == 200
    assert response.json() == {"keys": existing_keys}
    assert jwk_client.app.state.jwk_json == existing_keys[0]


def test_key_added_after_startup_without_key(jwk_client, signing_keys, monkeypatch):
    monkeypatch.delenv("JWTKEY")
    assert jwk_client.get("/jwk").json() == {"error": "JWK not configured on this server"}

    key = signing_keys[0]
    monkeypatch.setenv("JWTKEY", key.export_to_pem(private_key=True, password=None).decode())
    public_keys = jwk_client.get("/jwk").json()["keys"]
    assert len(public_keys) == 1
    assert public_keys[0]["kid"] == key.thumbprint()


def test_invalid_key_without_existing_chain_returns_configuration_error(jwk_client, monkeypatch):
    monkeypatch.setenv("JWTKEY", "invalid PEM")
    response = jwk_client.get("/jwk")
    assert response.status_code == 200
    assert response.json() == {"error": "JWK not configured on this server"}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_request", [True, False])
async def test_pds_signs_with_new_kid_before_nhs_fetches_jwk(jwk_client, signing_keys, monkeypatch, with_request):
    old_key, new_key, _ = signing_keys
    append_public_jwk(jwk_client.app, old_key.export_to_pem(private_key=True, password=None))
    monkeypatch.setenv("JWTKEY", new_key.export_to_pem(private_key=True, password=None).decode())

    async def nhs_token_endpoint(url, *, data):
        token = data["client_assertion"]
        kid = jwt.get_unverified_header(token)["kid"]
        # Signing must use the new key ID even before the public endpoint sees rotation.
        assert jwk_client.app.state.jwk_json["kid"] == old_key.thumbprint()
        assert kid == new_key.thumbprint()
        public_keys = jwk_client.get("/jwk").json()["keys"]
        assert {key["kid"] for key in public_keys} == {old_key.thumbprint(), kid}
        public_key = next(key for key in public_keys if key["kid"] == kid)
        claims = jwt.decode(token, jwk.JWK(**public_key).export_to_pem(), algorithms=["RS512"], audience=url)
        assert claims["sub"] == claims["iss"]
        return httpx.Response(200, json={"access_token": "verified-token", "expires_in": 300})

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock),
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None
        upstream = AsyncMock()
        upstream.post.side_effect = nhs_token_endpoint
        upstream.get.return_value = httpx.Response(
            200, json={"resourceType": "Patient"}, request=httpx.Request("GET", "https://example.test/patient")
        )
        factory.return_value.__aenter__.return_value = upstream
        request = Request({"type": "http", "app": jwk_client.app}) if with_request else None
        assert await lookup_patient(9690937278, request=request, saml=saml) == {"resourceType": "Patient"}
        upstream.post.assert_awaited_once()
        assert upstream.get.await_args.kwargs["headers"]["Authorization"] == "Bearer verified-token"


def test_pds_fallback_key_id_matches_signing_key(signing_keys, monkeypatch, tmp_path):
    monkeypatch.delenv("JWTKEY")
    monkeypatch.chdir(tmp_path)
    key = signing_keys[0]
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    (key_dir / "test-1.pem").write_bytes(key.export_to_pem(private_key=True, password=None))

    token = pds_jwt("test-issuer", "test-subject", "test-audience")
    assert jwt.get_unverified_header(token)["kid"] == key.thumbprint()
    claims = jwt.decode(token, key.export_to_pem(), algorithms=["RS512"], audience="test-audience")
    assert claims["iss"] == "test-issuer"
    assert claims["sub"] == "test-subject"
