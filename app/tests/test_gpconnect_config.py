import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.gpconnect import _fetch_gpconnect_record
from app.main import lifespan
from fastapi import FastAPI


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
        os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "1.5"}
    ):
        async with lifespan(app):
            pass  # Should not raise

    # Missing required config
    for var in ["API_KEY", "ORG_ASID", "ORG_CODE"]:
        env_dict = {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "4"}
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
        os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "invalid"}
    ):
        with pytest.raises(RuntimeError, match="Invalid CCDA_EXPIRY_HOURS configuration"):
            async with lifespan(app):
                pass

    with patch.dict(os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "-1"}):
        with pytest.raises(RuntimeError, match="Must be positive and finite"):
            async with lifespan(app):
                pass

    # Infinity
    with patch.dict(
        os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "inf"}
    ):
        with pytest.raises(RuntimeError, match="Must be positive and finite"):
            async with lifespan(app):
                pass

    # NaN
    with patch.dict(
        os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "nan"}
    ):
        with pytest.raises(RuntimeError, match="Must be positive and finite"):
            async with lifespan(app):
                pass

    # Oversized value (e.g. 100 years)
    with patch.dict(
        os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "876000"}
    ):
        with pytest.raises(RuntimeError, match="Duration exceeds maximum allowed cache expiry"):
            async with lifespan(app):
                pass

    # Sub-second expiry
    with patch.dict(
        os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00", "CCDA_EXPIRY_HOURS": "0.0001"}
    ):
        with pytest.raises(RuntimeError, match="Duration too small for Redis expiry"):
            async with lifespan(app):
                pass

    # Normal default value
    with patch.dict(os.environ, {"API_KEY": "test", "ORG_ASID": "123", "ORG_CODE": "RRV00"}):
        async with lifespan(app):
            pass
