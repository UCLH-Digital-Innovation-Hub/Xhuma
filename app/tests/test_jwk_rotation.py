import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jwcrypto import jwk

from app.main import get_jwk, lifespan
from app.pds.pds import lookup_patient
from app.security import _jwks_cache, clear_jwks_cache, get_jwk_keyset, pds_jwt
from app.tests.fixtures.saml_attributes import saml


class MockSecretProperty:
    def __init__(self, version, created_on=None, enabled=True, not_before=None, expires_on=None):
        self.version = version
        self.created_on = created_on or datetime.now(timezone.utc)
        self.enabled = enabled
        self.not_before = not_before
        self.expires_on = expires_on
        self.name = "jwtkey"


class MockKeyVaultSecret:
    def __init__(self, value, version):
        self.value = value
        self.name = "jwtkey"
        self.properties = MockSecretProperty(version)


class MockKeyVault:
    def __init__(self):
        self.versions = {}  # version -> (MockSecretProperty, str pem)
        self.list_calls = 0
        self.get_secret_calls = 0

    def add_version(self, version_id, pem_value, created_on=None, enabled=True, not_before=None, expires_on=None):
        prop = MockSecretProperty(
            version=version_id,
            created_on=created_on,
            enabled=enabled,
            not_before=not_before,
            expires_on=expires_on,
        )
        self.versions[version_id] = (prop, pem_value)

    def set_enabled(self, version_id, enabled: bool):
        if version_id in self.versions:
            self.versions[version_id][0].enabled = enabled

    def list_properties_of_secret_versions(self, secret_name):
        self.list_calls += 1
        return [prop for prop, _ in self.versions.values()]

    def get_secret(self, secret_name, version=None):
        self.get_secret_calls += 1
        if version is not None:
            if version in self.versions:
                _, val = self.versions[version]
                return MockKeyVaultSecret(val, version)
            raise RuntimeError(f"Secret version {version} not found")
        # If no version specified, return latest by created_on
        if not self.versions:
            raise RuntimeError("Secret not found")
        latest = max(self.versions.values(), key=lambda item: item[0].created_on)
        return MockKeyVaultSecret(latest[1], latest[0].version)


@pytest.fixture(autouse=True)
def reset_jwks_cache():
    clear_jwks_cache()
    yield
    clear_jwks_cache()


@pytest.fixture
def mock_kv(monkeypatch):
    kv = MockKeyVault()
    monkeypatch.setenv("KEY_VAULT_URL", "https://mock-vault.vault.azure.net")

    with (
        patch("app.security.ManagedIdentityCredential"),
        patch("app.security.SecretClient") as factory,
    ):
        factory.return_value.__enter__.return_value = kv
        yield kv


@pytest.fixture
def test_keys():
    """Generate 4 distinct 2048-bit RSA keys for test execution."""
    keys = []
    for _ in range(4):
        k = jwk.JWK.generate(kty="RSA", size=2048)
        pem = k.export_to_pem(private_key=True, password=None).decode("utf-8")
        keys.append((k, pem))
    return keys


@pytest.fixture
def test_app():
    app = FastAPI()
    app.state.jwk_json = {}
    app.state.jwk_keys = []
    app.add_api_route("/jwk", get_jwk)
    return app


# -------------------------------------------------------------------------
# Test 1: One Key Vault version -> One JWK
# -------------------------------------------------------------------------
def test_one_key_vault_version_returns_one_jwk(mock_kv, test_keys, test_app):
    k1, pem1 = test_keys[0]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        data = res.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        assert data["keys"][0]["kid"] == k1.thumbprint()


# -------------------------------------------------------------------------
# Test 2: Two enabled versions -> Current + Previous JWK
# -------------------------------------------------------------------------
def test_two_enabled_versions_returns_current_and_previous_jwk(mock_kv, test_keys, test_app):
    k1, pem1 = test_keys[0]
    k2, pem2 = test_keys[1]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)
    mock_kv.add_version("v2", pem2, created_on=t1)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 2
        # Current (v2) is first, previous (v1) is second
        assert keys[0]["kid"] == k2.thumbprint()
        assert keys[1]["kid"] == k1.thumbprint()


# -------------------------------------------------------------------------
# Test 3: Three enabled versions -> Only newest two published
# -------------------------------------------------------------------------
def test_three_enabled_versions_publishes_only_newest_two(mock_kv, test_keys, test_app):
    k1, pem1 = test_keys[0]
    k2, pem2 = test_keys[1]
    k3, pem3 = test_keys[2]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)
    mock_kv.add_version("v2", pem2, created_on=t1)
    mock_kv.add_version("v3", pem3, created_on=t2)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 2
        # Newest two are v3 and v2; v1 is discarded
        assert keys[0]["kid"] == k3.thumbprint()
        assert keys[1]["kid"] == k2.thumbprint()
        assert k1.thumbprint() not in [k["kid"] for k in keys]


# -------------------------------------------------------------------------
# Test 4: Current key alone signs new assertions
# -------------------------------------------------------------------------
def test_current_key_alone_signs_new_assertions(mock_kv, test_keys):
    k1, pem1 = test_keys[0]
    k2, pem2 = test_keys[1]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)
    mock_kv.add_version("v2", pem2, created_on=t1)

    token = pds_jwt("iss-test", "sub-test", "https://oauth.test/token")
    header = jwt.get_unverified_header(token)
    assert header["kid"] == k2.thumbprint()

    # Verify signature validates with v2 public key
    v2_pub_pem = k2.export_to_pem()
    decoded = jwt.decode(token, v2_pub_pem, algorithms=["RS512"], audience="https://oauth.test/token")
    assert decoded["sub"] == "sub-test"

    # Verify signature fails with v1 public key
    v1_pub_pem = k1.export_to_pem()
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(token, v1_pub_pem, algorithms=["RS512"], audience="https://oauth.test/token")


# -------------------------------------------------------------------------
# Test 5: JWT kid matches the current public JWK
# -------------------------------------------------------------------------
def test_jwt_kid_matches_current_public_jwk(mock_kv, test_keys, test_app):
    _, pem1 = test_keys[0]
    k2, pem2 = test_keys[1]
    mock_kv.add_version("v1", pem1, created_on=datetime(2026, 1, 1, tzinfo=timezone.utc))
    mock_kv.add_version("v2", pem2, created_on=datetime(2026, 1, 2, tzinfo=timezone.utc))

    token = pds_jwt("iss-test", "sub-test", "aud-test")
    token_kid = jwt.get_unverified_header(token)["kid"]

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        current_pub_kid = res.json()["keys"][0]["kid"]
        assert token_kid == current_pub_kid
        assert token_kid == k2.thumbprint()


# -------------------------------------------------------------------------
# Test 6: Previous key can still validate an assertion produced before rotation
# -------------------------------------------------------------------------
def test_previous_key_can_validate_assertion_produced_before_rotation(mock_kv, test_keys, test_app):
    k1, pem1 = test_keys[0]
    k2, pem2 = test_keys[1]
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)

    # Produce assertion before rotation (v1 is current)
    token_before_rotation = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token_before_rotation)["kid"] == k1.thumbprint()

    # Now rotate: create version v2 in Key Vault and clear cache
    t1 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    mock_kv.add_version("v2", pem2, created_on=t1)
    clear_jwks_cache()

    # Verify /jwk exposes both keys and the old assertion can be validated against /jwk
    with TestClient(test_app) as client:
        res = client.get("/jwk")
        jwks = res.json()["keys"]
        assert len(jwks) == 2

        # Find the JWK corresponding to the token's kid
        target_kid = jwt.get_unverified_header(token_before_rotation)["kid"]
        matching_key = next((k for k in jwks if k["kid"] == target_kid), None)
        assert matching_key is not None

        pub_pem = jwk.JWK(**matching_key).export_to_pem()
        decoded = jwt.decode(token_before_rotation, pub_pem, algorithms=["RS512"], audience="aud-test")
        assert decoded["sub"] == "sub-test"


# -------------------------------------------------------------------------
# Test 7: Disabling the previous version removes it from /jwk
# -------------------------------------------------------------------------
def test_disabling_previous_version_removes_it_from_jwk(mock_kv, test_keys, test_app):
    k1, pem1 = test_keys[0]
    k2, pem2 = test_keys[1]
    mock_kv.add_version("v1", pem1, created_on=datetime(2026, 1, 1, tzinfo=timezone.utc), enabled=True)
    mock_kv.add_version("v2", pem2, created_on=datetime(2026, 1, 2, tzinfo=timezone.utc), enabled=True)

    with TestClient(test_app) as client:
        res1 = client.get("/jwk")
        assert len(res1.json()["keys"]) == 2

        # Operator disables v1 in Key Vault
        mock_kv.set_enabled("v1", False)
        clear_jwks_cache()

        res2 = client.get("/jwk")
        keys = res2.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kid"] == k2.thumbprint()
        assert k1.thumbprint() not in [k["kid"] for k in keys]


# -------------------------------------------------------------------------
# Test 8: Staged rotation sequence with future not_before
# -------------------------------------------------------------------------
def test_staged_rotation_sequence(mock_kv, test_keys, test_app):
    """
    Test the complete operational staged rotation lifecycle:
    1. N current: /jwk => [N], signing => N
    2. Create N+1 with future not_before: /jwk => [N+1, N], signing => N
    3. not_before reached: /jwk => [N+1, N], signing => N+1
    4. Disable N after grace period: /jwk => [N+1], signing => N+1
    """
    k1, pem1 = test_keys[0]
    k2, pem2 = test_keys[1]

    # Step 1: N current
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0, enabled=True)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kid"] == k1.thumbprint()

    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k1.thumbprint()

    # Step 2: Create N+1 with future not_before
    t1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    future_nb = now + timedelta(hours=2)
    mock_kv.add_version("v2", pem2, created_on=t1, enabled=True, not_before=future_nb)
    clear_jwks_cache()

    # /jwk => [N+1, N]
    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 2
        assert keys[0]["kid"] == k2.thumbprint()
        assert keys[1]["kid"] == k1.thumbprint()

    # signing => N (v1), since v2 has not_before in the future
    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k1.thumbprint()

    # Step 3: not_before reached
    mock_kv.versions["v2"][0].not_before = now - timedelta(minutes=1)
    clear_jwks_cache()

    # /jwk => [N+1, N]
    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 2
        assert keys[0]["kid"] == k2.thumbprint()
        assert keys[1]["kid"] == k1.thumbprint()

    # signing => N+1 (v2)
    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k2.thumbprint()

    # Step 4: Disable N after grace period
    mock_kv.set_enabled("v1", False)
    clear_jwks_cache()

    # /jwk => [N+1]
    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kid"] == k2.thumbprint()

    # signing => N+1 (v2)
    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k2.thumbprint()


# -------------------------------------------------------------------------
# Test 9: Disabled and expired versions are ignored; future not_before is published
# -------------------------------------------------------------------------
def test_disabled_and_expired_versions_are_ignored(mock_kv, test_keys, test_app):
    k_disabled, pem_disabled = test_keys[0]
    k_expired, pem_expired = test_keys[1]
    k_valid, pem_valid = test_keys[2]

    now = datetime.now(timezone.utc)
    mock_kv.add_version("v_disabled", pem_disabled, created_on=now - timedelta(days=5), enabled=False)
    mock_kv.add_version(
        "v_expired",
        pem_expired,
        created_on=now - timedelta(days=4),
        enabled=True,
        expires_on=now - timedelta(hours=1),
    )
    mock_kv.add_version("v_valid", pem_valid, created_on=now - timedelta(days=2), enabled=True)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kid"] == k_valid.thumbprint()

    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k_valid.thumbprint()


# -------------------------------------------------------------------------
# Test 10: Fresh application instances return identical JWKS
# -------------------------------------------------------------------------
def test_fresh_application_instances_return_identical_jwks(mock_kv, test_keys):
    _, pem1 = test_keys[0]
    _, pem2 = test_keys[1]
    mock_kv.add_version("v1", pem1, created_on=datetime(2026, 1, 1, tzinfo=timezone.utc))
    mock_kv.add_version("v2", pem2, created_on=datetime(2026, 1, 2, tzinfo=timezone.utc))

    # App instance A
    app_a = FastAPI()
    app_a.add_api_route("/jwk", get_jwk)
    with TestClient(app_a) as client_a:
        res_a = client_a.get("/jwk").json()

    # Clear in-memory cache to simulate separate process for App instance B
    clear_jwks_cache()

    # App instance B (completely fresh instance reconstructing from Key Vault)
    app_b = FastAPI()
    app_b.add_api_route("/jwk", get_jwk)
    with TestClient(app_b) as client_b:
        res_b = client_b.get("/jwk").json()

    assert res_a == res_b
    assert len(res_a["keys"]) == 2


# -------------------------------------------------------------------------
# Test 11: Restart reconstructs the identical keyset
# -------------------------------------------------------------------------
def test_restart_reconstructs_identical_keyset(mock_kv, test_keys):
    _, pem1 = test_keys[0]
    _, pem2 = test_keys[1]
    mock_kv.add_version("v1", pem1, created_on=datetime(2026, 1, 1, tzinfo=timezone.utc))
    mock_kv.add_version("v2", pem2, created_on=datetime(2026, 1, 2, tzinfo=timezone.utc))

    # Prior to restart
    keyset_before = get_jwk_keyset()

    # Simulate restart by clearing cache
    clear_jwks_cache()

    fresh_app = FastAPI(lifespan=lifespan)
    fresh_app.add_api_route("/jwk", get_jwk)
    with TestClient(fresh_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keyset_after = res.json()["keys"]

    assert keyset_before == keyset_after


# -------------------------------------------------------------------------
# Test 12: No RSA private parameters appear in /jwk
# -------------------------------------------------------------------------
def test_no_rsa_private_parameters_appear_in_jwk(mock_kv, test_keys, test_app):
    _, pem1 = test_keys[0]
    _, pem2 = test_keys[1]
    mock_kv.add_version("v1", pem1, created_on=datetime(2026, 1, 1, tzinfo=timezone.utc))
    mock_kv.add_version("v2", pem2, created_on=datetime(2026, 1, 2, tzinfo=timezone.utc))

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        keys = res.json()["keys"]
        forbidden_params = {"d", "p", "q", "dp", "dq", "qi", "dmp1", "dmq1", "coeff", "oth", "k"}
        for k in keys:
            assert k["alg"] == "RS512"
            assert k["use"] == "sig"
            assert not (forbidden_params & k.keys())


# -------------------------------------------------------------------------
# Test 13: Key Vault failure causes signing to fail closed
# -------------------------------------------------------------------------
def test_key_vault_failure_causes_signing_to_fail_closed(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URL", "https://unreachable-vault.vault.azure.net")
    with (
        patch("app.security.ManagedIdentityCredential"),
        patch("app.security.SecretClient") as factory,
    ):
        factory.return_value.__enter__.side_effect = RuntimeError("Key Vault network connection failed")

        with pytest.raises(RuntimeError, match="Key Vault network connection failed"):
            pds_jwt("iss-test", "sub-test", "aud-test")


# -------------------------------------------------------------------------
# Test 14: Key Vault failure does not cause a production/local-key fallback
# -------------------------------------------------------------------------
def test_key_vault_failure_does_not_cause_local_fallback(monkeypatch, test_keys, test_app):
    _, pem1 = test_keys[0]
    # Set local fallback key in environment
    monkeypatch.setenv("JWTKEY", pem1)
    # But configure KEY_VAULT_URL
    monkeypatch.setenv("KEY_VAULT_URL", "https://failing-vault.vault.azure.net")

    with (
        patch("app.security.ManagedIdentityCredential"),
        patch("app.security.SecretClient") as factory,
    ):
        factory.return_value.__enter__.side_effect = RuntimeError("AccessToKeyVaultDenied")

        # Signing must fail closed, NOT fallback to JWTKEY
        with pytest.raises(RuntimeError):
            pds_jwt("iss-test", "sub-test", "aud-test")

        # /jwk endpoint must NOT serve the local key and must return HTTP 503
        with TestClient(test_app) as client:
            res = client.get("/jwk")
            assert res.status_code == 503
            assert res.json() == {"detail": "JWKS unavailable"}


# -------------------------------------------------------------------------
# Test 15: Local JWTKEY/test-key behaviour still works when KEY_VAULT_URL is absent
# -------------------------------------------------------------------------
def test_local_jwtkey_behaviour_works_when_key_vault_url_absent(monkeypatch, test_keys, test_app, tmp_path):
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    k1, pem1 = test_keys[0]

    # Subtest 1: JWTKEY environment variable
    monkeypatch.setenv("JWTKEY", pem1)
    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kid"] == k1.thumbprint()

    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k1.thumbprint()

    # Subtest 2: Fallback to keys/test-1.pem in dev/local mode
    clear_jwks_cache()
    monkeypatch.delenv("JWTKEY")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.chdir(tmp_path)
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    (keys_dir / "test-1.pem").write_text(pem1)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        assert res.json()["keys"][0]["kid"] == k1.thumbprint()


# -------------------------------------------------------------------------
# Test 16: Bounded JWKS cache avoids repeated Key Vault calls within TTL
# -------------------------------------------------------------------------
def test_jwks_cache_ttl_avoids_repeated_key_vault_calls(mock_kv, test_keys, test_app):
    k1, pem1 = test_keys[0]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)

    with TestClient(test_app) as client:
        for _ in range(5):
            res = client.get("/jwk")
            assert res.status_code == 200
            assert res.json()["keys"][0]["kid"] == k1.thumbprint()

    # Key Vault must only have been queried once
    assert mock_kv.list_calls == 1
    assert mock_kv.get_secret_calls == 1


# -------------------------------------------------------------------------
# Test 17: JWKS cache refreshes after TTL expiration
# -------------------------------------------------------------------------
def test_jwks_cache_refreshes_after_ttl(mock_kv, test_keys, test_app, monkeypatch):
    k1, pem1 = test_keys[0]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)

    monkeypatch.setenv("JWKS_CACHE_TTL_SECONDS", "60")

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        assert mock_kv.list_calls == 1

        # Simulate TTL expiration by adjusting cached_at
        _jwks_cache.cached_at -= 65

        res = client.get("/jwk")
        assert res.status_code == 200
        assert mock_kv.list_calls == 2


# -------------------------------------------------------------------------
# Test 18: JWKS cache serves last-known-good on Key Vault refresh failure
# -------------------------------------------------------------------------
def test_jwks_cache_serves_last_known_good_on_refresh_failure(mock_kv, test_keys, test_app, caplog):
    k1, pem1 = test_keys[0]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)

    with TestClient(test_app) as client:
        # Initial successful call warms the cache
        res = client.get("/jwk")
        assert res.status_code == 200
        assert res.json()["keys"][0]["kid"] == k1.thumbprint()

        # Age the cache past TTL
        _jwks_cache.cached_at -= 100

        # Simulate subsequent Key Vault failure on refresh
        def _failing_list(name):
            raise RuntimeError("Key Vault transient network timeout")

        mock_kv.list_properties_of_secret_versions = _failing_list

        with caplog.at_level(logging.WARNING):
            res2 = client.get("/jwk")
            assert res2.status_code == 200
            assert res2.json()["keys"][0]["kid"] == k1.thumbprint()
            assert any("Failed to refresh public JWKS" in r.message for r in caplog.records)


# -------------------------------------------------------------------------
# Test 19: /jwk returns HTTP 503 when no public JWKS has ever been obtained
# -------------------------------------------------------------------------
def test_jwks_returns_503_when_no_keys_ever_obtained(mock_kv, test_app):
    # Key Vault has no secrets and cache is empty
    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 503
        assert res.json() == {"detail": "JWKS unavailable"}


# -------------------------------------------------------------------------
# Test 20: JWKS cache never stores private PEM material
# -------------------------------------------------------------------------
def test_jwks_cache_never_stores_private_pem_material(mock_kv, test_keys, test_app):
    _, pem1 = test_keys[0]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem1, created_on=t0)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200

    assert _jwks_cache.public_keys is not None
    for key_dict in _jwks_cache.public_keys:
        assert "private_pem" not in key_dict
        for forbidden in ("d", "p", "q", "dp", "dq", "qi", "dmp1", "dmq1", "coeff", "oth", "k"):
            assert forbidden not in key_dict


# -------------------------------------------------------------------------
# Integration test: PDS lookup interaction with dynamic JWK
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pds_lookup_flow_with_key_vault(mock_kv, test_keys, test_app):
    k1, pem1 = test_keys[0]
    mock_kv.add_version("v1", pem1, created_on=datetime(2026, 1, 1, tzinfo=timezone.utc))

    async def mock_nhs_oauth(url, *, data):
        token = data["client_assertion"]
        kid = jwt.get_unverified_header(token)["kid"]
        assert kid == k1.thumbprint()
        claims = jwt.decode(token, k1.export_to_pem(), algorithms=["RS512"], audience=url)
        assert claims["sub"] == claims["iss"]
        return httpx.Response(200, json={"access_token": "verified-token", "expires_in": 300})

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock),
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None
        upstream = AsyncMock()
        upstream.post.side_effect = mock_nhs_oauth
        upstream.get.return_value = httpx.Response(
            200, json={"resourceType": "Patient"}, request=httpx.Request("GET", "https://example.test/patient")
        )
        factory.return_value.__aenter__.return_value = upstream

        req = Request({"type": "http", "app": test_app})
        res = await lookup_patient(9690937278, request=req, saml=saml)
        assert res == {"resourceType": "Patient"}


# -------------------------------------------------------------------------
# Test 22: Deduplication - Two secret versions containing identical key material -> one JWKS entry
# -------------------------------------------------------------------------
def test_two_versions_with_identical_key_material_publishes_one_jwk(mock_kv, test_keys, test_app):
    k_a, pem_a = test_keys[0]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem_a, created_on=t0)
    mock_kv.add_version("v2", pem_a, created_on=t1)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kid"] == k_a.thumbprint()

    # Signing uses the newest version
    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k_a.thumbprint()


# -------------------------------------------------------------------------
# Test 23: Deduplication - Newest two versions both key A, older version key B -> [A, B]
# -------------------------------------------------------------------------
def test_newest_two_identical_older_distinct_publishes_a_b(mock_kv, test_keys, test_app):
    k_a, pem_a = test_keys[0]
    k_b, pem_b = test_keys[1]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem_b, created_on=t0)
    mock_kv.add_version("v2", pem_a, created_on=t1)
    mock_kv.add_version("v3", pem_a, created_on=t2)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 2
        assert keys[0]["kid"] == k_a.thumbprint()
        assert keys[1]["kid"] == k_b.thumbprint()

    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k_a.thumbprint()


# -------------------------------------------------------------------------
# Test 24: Deduplication - A/A/B/C versions -> [A, B]
# -------------------------------------------------------------------------
def test_four_versions_a_a_b_c_publishes_a_b(mock_kv, test_keys, test_app):
    k_a, pem_a = test_keys[0]
    k_b, pem_b = test_keys[1]
    k_c, pem_c = test_keys[2]
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 1, 4, 10, 0, tzinfo=timezone.utc)
    mock_kv.add_version("v1", pem_c, created_on=t0)
    mock_kv.add_version("v2", pem_b, created_on=t1)
    mock_kv.add_version("v3", pem_a, created_on=t2)
    mock_kv.add_version("v4", pem_a, created_on=t3)

    with TestClient(test_app) as client:
        res = client.get("/jwk")
        assert res.status_code == 200
        keys = res.json()["keys"]
        assert len(keys) == 2
        assert keys[0]["kid"] == k_a.thumbprint()
        assert keys[1]["kid"] == k_b.thumbprint()
        assert k_c.thumbprint() not in [k["kid"] for k in keys]

    token = pds_jwt("iss-test", "sub-test", "aud-test")
    assert jwt.get_unverified_header(token)["kid"] == k_a.thumbprint()
