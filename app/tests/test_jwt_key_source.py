import asyncio
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from jwcrypto import jwk

from app.main import get_jwk
from app.security import clear_jwks_cache, get_local_key, get_signing_key
from app.tests.conftest import TEST_PRIVATE_KEY

VAULT_URL = "https://test-vault.vault.azure.net"


@pytest.fixture(autouse=True)
def reset_jwks_cache():
    clear_jwks_cache()
    yield
    clear_jwks_cache()


def test_key_vault_reads_latest_secret_with_managed_identity(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URL", VAULT_URL)
    with patch("app.security.ManagedIdentityCredential") as identity, patch("app.security.SecretClient") as factory:
        client = factory.return_value.__enter__.return_value
        first = SimpleNamespace(
            version="v1", enabled=True, expires_on=None, not_before=None, created_on=datetime(2026, 1, 1, tzinfo=UTC)
        )
        second = SimpleNamespace(
            version="v2", enabled=True, expires_on=None, not_before=None, created_on=datetime(2026, 1, 2, tzinfo=UTC)
        )
        client.list_properties_of_secret_versions.return_value = [first]
        client.get_secret.return_value.value = TEST_PRIVATE_KEY.replace("\n", "\\n")
        pem, kid = get_signing_key()
        assert pem == TEST_PRIVATE_KEY
        assert kid == jwk.JWK.from_pem(pem.encode()).thumbprint()
        client.get_secret.assert_called_with("jwtkey", version="v1")

        second_key = jwk.JWK.generate(kty="RSA", size=2048)
        second_pem = second_key.export_to_pem(private_key=True, password=None).decode()
        client.list_properties_of_secret_versions.return_value = [first, second]
        client.get_secret.return_value.value = second_pem
        assert get_signing_key() == (second_pem, second_key.thumbprint())

        factory.assert_called_with(vault_url=VAULT_URL, credential=identity.return_value.__enter__.return_value)
        assert client.get_secret.call_count == 2
        client.get_secret.assert_called_with("jwtkey", version="v2")
        assert identity.return_value.__exit__.call_count == 2
        assert factory.return_value.__exit__.call_count == 2


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_empty_vault_secret_does_not_fall_back_to_environment(monkeypatch, secret):
    monkeypatch.setenv("KEY_VAULT_URL", VAULT_URL)
    with patch("app.security.ManagedIdentityCredential"), patch("app.security.SecretClient") as factory:
        client = factory.return_value.__enter__.return_value
        client.list_properties_of_secret_versions.return_value = [
            SimpleNamespace(version="v1", enabled=True, expires_on=None, not_before=None, created_on=None)
        ]
        client.get_secret.return_value.value = secret
        with pytest.raises(RuntimeError, match="has no value"):
            get_signing_key()


def test_vault_failure_does_not_fall_back_to_local_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("KEY_VAULT_URL", VAULT_URL)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys/test-1.pem").write_text("local testing key")
    with patch("app.security.ManagedIdentityCredential"), patch("app.security.SecretClient") as factory:
        factory.return_value.__enter__.return_value.list_properties_of_secret_versions.side_effect = RuntimeError(
            "Vault unavailable"
        )
        with pytest.raises(RuntimeError, match="Vault unavailable"):
            get_signing_key()


@pytest.mark.parametrize("environment", ["dev", "local"])
def test_local_file_fallback(monkeypatch, tmp_path, environment):
    monkeypatch.delenv("JWTKEY")
    monkeypatch.setenv("ENV", environment)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys/test-1.pem").write_text(TEST_PRIVATE_KEY.replace("\n", "\\n"))
    pem, kid = get_signing_key()
    assert pem == TEST_PRIVATE_KEY
    assert kid == jwk.JWK.from_pem(pem.encode()).thumbprint()


def test_production_does_not_use_local_test_file(monkeypatch, tmp_path):
    monkeypatch.delenv("JWTKEY")
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys/test-1.pem").write_text("local testing key")
    assert get_local_key() is None


def test_failed_vault_refresh_preserves_public_chain(monkeypatch):
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_api_route("/jwk", get_jwk)
    with TestClient(app) as client:
        original = client.get("/jwk").json()
        monkeypatch.setenv("KEY_VAULT_URL", VAULT_URL)
        monkeypatch.setenv("JWKS_CACHE_TTL_SECONDS", "0")
        with patch("app.security.ManagedIdentityCredential"), patch("app.security.SecretClient") as factory:
            factory.return_value.__enter__.return_value.list_properties_of_secret_versions.side_effect = RuntimeError(
                "Vault unavailable"
            )
            assert client.get("/jwk").json() == original
            assert app.state.jwk_json == original["keys"][0]


@pytest.mark.asyncio
async def test_key_vault_wait_does_not_block_request_loop(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URL", VAULT_URL)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = Event()

    def paused_versions(name):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=3)
        return [SimpleNamespace(version="v1", enabled=True, expires_on=None, not_before=None, created_on=None)]

    with patch("app.security.ManagedIdentityCredential"), patch("app.security.SecretClient") as factory:
        client = factory.return_value.__enter__.return_value
        client.list_properties_of_secret_versions.side_effect = paused_versions
        client.get_secret.return_value.value = TEST_PRIVATE_KEY
        app = FastAPI()
        task = asyncio.create_task(get_jwk(Request({"type": "http", "app": app})))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert not task.done()
        finally:
            release.set()
            result = await task
        assert len(result["keys"]) == 1
