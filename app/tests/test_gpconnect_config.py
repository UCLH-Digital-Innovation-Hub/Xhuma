import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.gpconnect import _fetch_gpconnect_record
from app.main import lifespan

pytestmark = pytest.mark.usefixtures("mock_self_issuer")


@pytest.fixture(autouse=True)
def isolate_startup_io():
    # These tests exercise configuration/lifecycle, not live Redis or OTLP exporters.
    with (
        patch("app.main.redis_client", autospec=True),
        patch("app.main.snomed_client", autospec=True),
        patch("app.main.OTLPMetricExporter"),
        patch("app.main.PeriodicExportingMetricReader"),
        patch("app.main.MeterProvider"),
        patch("app.main.metrics.set_meter_provider"),
    ):
        yield


@pytest.mark.asyncio
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.redis_client.pipeline")
@patch("app.gpconnect.create_nhs_ssl_context")
@patch("app.gpconnect.httpx.AsyncClient")
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
async def test_ccda_expiry_configuration_parsing(
    mock_audit, mock_client, mock_ssl, mock_pipeline, mock_sds, mock_lookup
):
    pipeline = mock_pipeline.return_value
    pipeline.__aenter__.return_value = pipeline
    pipeline.execute = AsyncMock()

    # Setup happy path to get down to cache logic
    async def mock_lookup_patient(*args, **kwargs):
        return {
            "meta": {"security": [{"code": "U"}]},
            "generalPractitioner": [{"identifier": {"value": "ods1"}}],
        }

    mock_lookup.side_effect = mock_lookup_patient

    async def mock_sds_trace(*args, **kwargs):
        if kwargs.get("endpoint"):
            return {"entry": [{"resource": {"address": "test-url"}}]}
        return {
            "entry": [
                {
                    "resource": {
                        "identifier": [
                            {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "123"},
                            {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "123"},
                        ]
                    }
                }
            ]
        }

    mock_sds.side_effect = mock_sds_trace

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"resourceType": "Bundle", "type": "searchset", "entry": []}'
    mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

    from app.audit.models import SAMLAttributes
    from app.ccda.models.datatypes import CD

    saml = SAMLAttributes(subject_id="user1", organization="org1", organization_id="orgid1", role=CD(code="code"))
    request = MagicMock()
    del request.app

    with patch("app.gpconnect.convert_bundle") as mock_convert:

        async def dummy_convert(*args, **kwargs):
            return {"ClinicalDocument": "test"}

        mock_convert.side_effect = dummy_convert

        with patch.dict(
            os.environ, {"CCDA_EXPIRY_HOURS": "1.5", "ORG_ASID": "123", "API_KEY": "test", "ORG_CODE": "RRV00"}
        ):
            await _fetch_gpconnect_record(9690937278, saml, request=request)

            # Verify pipeline was called with timedelta(hours=1.5)
            mock_pipeline.return_value.setex.assert_called()
            # We can inspect the arguments for the first setex call
            args, kwargs = mock_pipeline.return_value.setex.call_args_list[0]
            assert args[1].total_seconds() == 1.5 * 3600


@pytest.mark.asyncio
async def test_startup_config_validation():
    app = FastAPI()

    # Happy path
    with patch.dict(
        os.environ,
        {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "1.5",
        },
    ):
        async with lifespan(app):
            pass  # Should not raise

    # Missing required config
    for var in ["API_KEY", "NHS_API_KEY", "ORG_ASID", "ORG_CODE"]:
        env_dict = {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "4",
        }
        env_dict.pop(var)
        with patch.dict(os.environ, env_dict, clear=True):
            with pytest.raises(RuntimeError, match=f"Missing required configuration: {var}"):
                async with lifespan(app):
                    pass

        # Empty string config
        env_dict[var] = "   "
        with patch.dict(os.environ, env_dict, clear=True):
            with pytest.raises(RuntimeError, match=f"Missing required configuration: {var}"):
                async with lifespan(app):
                    pass

    # Invalid expiry
    with patch.dict(
        os.environ,
        {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "invalid",
        },
    ):
        with pytest.raises(RuntimeError, match="Invalid CCDA_EXPIRY_HOURS configuration"):
            async with lifespan(app):
                pass

    with patch.dict(
        os.environ,
        {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "-1",
        },
    ):
        with pytest.raises(RuntimeError, match="Must be positive and finite"):
            async with lifespan(app):
                pass

    # Infinity
    with patch.dict(
        os.environ,
        {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "inf",
        },
    ):
        with pytest.raises(RuntimeError, match="Must be positive and finite"):
            async with lifespan(app):
                pass

    # NaN
    with patch.dict(
        os.environ,
        {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "nan",
        },
    ):
        with pytest.raises(RuntimeError, match="Must be positive and finite"):
            async with lifespan(app):
                pass

    # Oversized value (e.g. 100 years)
    with patch.dict(
        os.environ,
        {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "876000",
        },
    ):
        with pytest.raises(RuntimeError, match="Duration exceeds maximum allowed cache expiry"):
            async with lifespan(app):
                pass

    # Sub-second expiry
    with patch.dict(
        os.environ,
        {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
            "CCDA_EXPIRY_HOURS": "0.0001",
        },
    ):
        with pytest.raises(RuntimeError, match="Duration too small for Redis expiry"):
            async with lifespan(app):
                pass

    # Normal default value (explicitly remove CCDA_EXPIRY_HOURS if set by conftest)
    env_vars = {"API_KEY": "test", "NHS_API_KEY": "test-nhs", "ORG_ASID": "123", "ORG_CODE": "RRV00"}
    with patch.dict(os.environ, env_vars), patch.dict(os.environ, clear=False):
        if "CCDA_EXPIRY_HOURS" in os.environ:
            del os.environ["CCDA_EXPIRY_HOURS"]
        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_lifespan_unresolved_keyvault_secret():
    """Test that application startup fails if Azure Key Vault fails to resolve a required secret."""
    app = FastAPI()

    for var in ["API_KEY", "NHS_API_KEY"]:
        base_dict = {
            "API_KEY": "test",
            "NHS_API_KEY": "test-nhs",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
        }
        # Standard format
        base_dict[var] = f"@Microsoft.KeyVault(SecretUri=https://xhuma.vault.azure.net/secrets/{var.lower()}/)"
        with patch.dict(os.environ, base_dict, clear=True):
            with pytest.raises(RuntimeError, match=f"Unresolved KeyVault reference for required configuration: {var}"):
                async with lifespan(app):
                    pass

        # With surrounding whitespace
        base_dict[var] = f"   @Microsoft.KeyVault(SecretUri=https://xhuma.vault.azure.net/secrets/{var.lower()}/)  "
        with patch.dict(os.environ, base_dict, clear=True):
            with pytest.raises(RuntimeError, match=f"Unresolved KeyVault reference for required configuration: {var}"):
                async with lifespan(app):
                    pass


@pytest.mark.asyncio
async def test_nhs_api_key_startup_validation_requirements():
    """Regression test verifying:
    1. missing NHS_API_KEY prevents startup;
    2. blank NHS_API_KEY prevents startup;
    3. unresolved Key Vault reference for NHS_API_KEY prevents startup;
    4. valid distinct API_KEY and NHS_API_KEY allow startup past this validation;
    5. runtime PDS/SDS code still has no fallback from NHS_API_KEY to API_KEY.
    """
    app = FastAPI()
    base_env = {
        "API_KEY": "xhuma-internal-secret",
        "NHS_API_KEY": "nhs-platform-credential",
        "ORG_ASID": "123",
        "ORG_CODE": "RRV00",
    }

    # 1. missing NHS_API_KEY prevents startup
    missing_env = dict(base_env)
    del missing_env["NHS_API_KEY"]
    with patch.dict(os.environ, missing_env, clear=True):
        with pytest.raises(RuntimeError, match="Missing required configuration: NHS_API_KEY"):
            async with lifespan(app):
                pass

    # 2. blank NHS_API_KEY prevents startup
    for blank_val in ["", "   ", "\t\n"]:
        blank_env = dict(base_env)
        blank_env["NHS_API_KEY"] = blank_val
        with patch.dict(os.environ, blank_env, clear=True):
            with pytest.raises(RuntimeError, match="Missing required configuration: NHS_API_KEY"):
                async with lifespan(app):
                    pass

    # 3. unresolved Key Vault reference for NHS_API_KEY prevents startup
    for kv_ref in [
        "@Microsoft.KeyVault(VaultName=xhuma-kv;SecretName=nhs-api-key)",
        "  @Microsoft.KeyVault(SecretUri=https://xhuma.vault.azure.net/secrets/nhs-api-key/) ",
    ]:
        kv_env = dict(base_env)
        kv_env["NHS_API_KEY"] = kv_ref
        with patch.dict(os.environ, kv_env, clear=True):
            with pytest.raises(
                RuntimeError, match="Unresolved KeyVault reference for required configuration: NHS_API_KEY"
            ):
                async with lifespan(app):
                    pass

    # 4. valid distinct API_KEY and NHS_API_KEY allow startup past this validation
    with patch.dict(os.environ, base_env, clear=True):
        async with lifespan(app):
            pass

    # 5. runtime PDS/SDS code still has no fallback from NHS_API_KEY to API_KEY
    from app.pds.pds import sds_trace

    no_fallback_env = {
        "API_KEY": "xhuma-internal-secret",
        "ORG_ASID": "123",
        "ORG_CODE": "RRV00",
    }
    with patch.dict(os.environ, no_fallback_env, clear=True):
        with patch("app.pds.pds.redis_client", autospec=True) as mock_redis:
            mock_redis.get.return_value = None
            with pytest.raises(ValueError, match="NHS_API_KEY environment variable is not set"):
                await sds_trace("RRV00")


@pytest.mark.asyncio
async def test_api_key_separation_prod_fails():
    app = FastAPI()
    with patch.dict(
        os.environ,
        {
            "ENV": "prod",
            "API_KEY": "same-secret",
            "NHS_API_KEY": "same-secret",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
        },
    ):
        with pytest.raises(RuntimeError, match="CRITICAL: API_KEY and NHS_API_KEY must not be identical"):
            async with lifespan(app):
                pass


@pytest.mark.asyncio
async def test_api_key_separation_dev_warns(capsys):
    app = FastAPI()
    with patch.dict(
        os.environ,
        {
            "ENV": "dev",
            "API_KEY": "same-secret",
            "NHS_API_KEY": "same-secret",
            "ORG_ASID": "123",
            "ORG_CODE": "RRV00",
        },
    ):
        async with lifespan(app):
            pass

    captured = capsys.readouterr()
    assert "Warning: API_KEY and NHS_API_KEY are identical" in captured.out
