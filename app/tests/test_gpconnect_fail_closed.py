import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request


def get_mock_request():
    request = Request(scope={"type": "http", "method": "GET"})
    return request


@pytest.mark.asyncio
async def test_gpconnect_env_prod_missing_relay_fails_closed(monkeypatch, mock_self_issuer, caplog):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("USE_RELAY", "1")
    monkeypatch.delenv("NHS_RELAY_BASE_PATH", raising=False)
    monkeypatch.delenv("NHS_OVER_INTERNET_PATH", raising=False)
    monkeypatch.setenv("ORG_ASID", "123")
    monkeypatch.setenv("ORG_CODE", "RRV00")

    from importlib import reload

    import app.settings

    reload(app.settings)
    import app.gpconnect

    reload(app.gpconnect)
    from app.gpconnect import gpconnect

    try:
        with (
            patch(
                "app.gpconnect.lookup_self_issuer", new_callable=AsyncMock, return_value="https://consumer.test/spine"
            ),
            patch("app.gpconnect.lookup_patient", new_callable=AsyncMock) as mock_lookup,
            patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as mock_sds,
            patch("app.gpconnect.attempt_audit", new_callable=AsyncMock),
            patch("app.gpconnect.create_jwt", return_value="fake_jwt"),
        ):
            # Valid restricted patient PDS response
            mock_lookup.return_value = {
                "meta": {"security": [{"code": "U"}]},
                "generalPractitioner": [{"identifier": {"value": "GP1"}}],
            }

            # Valid SDS endpoint and device trace
            mock_sds.side_effect = [
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Endpoint",
                                "address": "https://endpoint.nhs.uk",
                                "identifier": [
                                    {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "222"},
                                ],
                            }
                        }
                    ],
                },
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Device",
                                "identifier": [
                                    {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "111"},
                                ],
                            }
                        }
                    ],
                },
            ]

            # Valid SAML body for request
            saml_body = {
                "subject_id": "test",
                "organization": "test",
                "organization_id": "test",
                "home_community_id": "test",
                "role": "test",
                "role_code": "test",
                "purpose_of_use": "test",
            }

            # The transport logic should raise ValueError for missing relay path,
            # which is caught by GP Connect exception handler and returned as 502 with transport error.
            result = await gpconnect(9692140466, saml_attrs=saml_body, request=get_mock_request())

            assert result.status_code == 502
            body = json.loads(result.body)
            assert "Transport error" in body["error"]
            assert "step: gpconnect_request.transport_configuration, exception_type: ValueError" in caplog.text
    finally:
        monkeypatch.undo()
        reload(app.settings)
        reload(app.gpconnect)
