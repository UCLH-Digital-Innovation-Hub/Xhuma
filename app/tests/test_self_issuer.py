import json
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest

from app.pds.pds import GP_CONNECT_INTERACTION_ID, lookup_self_issuer, sds_cache_key
from app.security import create_jwt
from app.tests.fixtures.saml_attributes import saml

ISSUER = "https://consumer.test/spine"
XHUMA_ODS = "Z6G1Z"
PARTY_KEY = "Z6G1Z-123456"


def device_entry(asid="123", partykey=PARTY_KEY):
    return {
        "fullUrl": "https://int.api.service.nhs.uk/spine-directory/FHIR/R4/Device/consumer-device",
        "resource": {
            "resourceType": "Device",
            "identifier": [
                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": asid},
                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": partykey},
            ],
        },
    }


def endpoint_entry(address=ISSUER, status="active", partykey=PARTY_KEY):
    return {
        "resource": {
            "resourceType": "Endpoint",
            "status": status,
            "address": address,
            "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": partykey}],
        }
    }


def bundle(*entries):
    return {"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": list(entries)}


@pytest.fixture(autouse=True)
def self_identity(monkeypatch):
    monkeypatch.setenv("ORG_CODE", "RRV00")
    monkeypatch.setenv("ORG_ASID", "123")
    monkeypatch.setenv("XHUMA_ODS_CODE", XHUMA_ODS)
    monkeypatch.delenv("XHUMA_MHS_PARTY_KEY", raising=False)


@pytest.mark.asyncio
async def test_self_lookup_discovers_partykey_and_caches_both_queries():
    cached = {}

    async def cache_write(key, ttl, value):
        cached[key] = value.encode()

    with patch("app.pds.pds.redis_client", autospec=True) as cache, patch("app.pds.pds.httpx.AsyncClient") as factory:
        cache.get.side_effect = cached.get
        cache.setex.side_effect = cache_write
        client = AsyncMock()
        client.get.side_effect = [
            httpx.Response(200, json=bundle(device_entry("999", "other-party"), device_entry())),
            httpx.Response(200, json=bundle(endpoint_entry())),
        ]
        factory.return_value.__aenter__.return_value = client

        assert await lookup_self_issuer() == ISSUER
        assert await lookup_self_issuer() == ISSUER
        assert client.get.await_count == 2
        device_call, endpoint_call = client.get.await_args_list
        assert device_call.args[0].endswith("/Device")
        assert device_call.kwargs["params"] == {
            "organization": f"https://fhir.nhs.uk/Id/ods-organization-code|{XHUMA_ODS}",
            "identifier": [f"https://fhir.nhs.uk/Id/nhsServiceInteractionId|{GP_CONNECT_INTERACTION_ID}"],
        }
        assert endpoint_call.args[0].endswith("/Endpoint")
        assert endpoint_call.kwargs["params"] == {
            "organization": f"https://fhir.nhs.uk/Id/ods-organization-code|{XHUMA_ODS}",
            "identifier": [f"https://fhir.nhs.uk/Id/nhsMhsPartyKey|{PARTY_KEY}"],
        }
        assert device_call.kwargs["headers"]["apikey"] == "test-api-key"
        assert len(cached) == 2
        assert sds_cache_key(XHUMA_ODS, True, PARTY_KEY, None) in cached


@pytest.mark.asyncio
async def test_configured_partykey_filters_device_query(monkeypatch):
    monkeypatch.setenv("XHUMA_MHS_PARTY_KEY", PARTY_KEY)
    with patch("app.pds.pds.redis_client", autospec=True) as cache, patch("app.pds.pds.httpx.AsyncClient") as factory:
        cache.get.return_value = None
        client = AsyncMock()
        client.get.side_effect = [
            httpx.Response(200, json=bundle(device_entry())),
            httpx.Response(200, json=bundle(endpoint_entry())),
        ]
        factory.return_value.__aenter__.return_value = client
        assert await lookup_self_issuer() == ISSUER
        assert client.get.await_args_list[0].kwargs["params"]["identifier"] == [
            f"https://fhir.nhs.uk/Id/nhsServiceInteractionId|{GP_CONNECT_INTERACTION_ID}",
            f"https://fhir.nhs.uk/Id/nhsMhsPartyKey|{PARTY_KEY}",
        ]
        cache.setex.assert_any_await(
            sds_cache_key(XHUMA_ODS, partykey=PARTY_KEY), 12 * 3600, json.dumps(bundle(device_entry()))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trace",
    [
        bundle(),
        bundle(device_entry("999")),
        bundle(device_entry(), device_entry()),
        bundle(device_entry(partykey="")),
        {"resourceType": "OperationOutcome"},
    ],
)
async def test_invalid_self_device_stops_before_endpoint_lookup(trace):
    with patch("app.pds.pds.sds_trace", new_callable=AsyncMock, return_value=trace) as lookup:
        with pytest.raises(ValueError):
            await lookup_self_issuer()
        lookup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trace",
    [
        bundle(),
        bundle(endpoint_entry(status="off")),
        bundle(endpoint_entry(partykey="other-party")),
        bundle(endpoint_entry(address="relative/path")),
        bundle(endpoint_entry(address="")),
        bundle(endpoint_entry(), endpoint_entry(address="https://other.test/spine")),
        {"resourceType": "OperationOutcome"},
    ],
)
async def test_missing_invalid_or_ambiguous_self_endpoint_is_rejected(trace):
    with patch("app.pds.pds.sds_trace", new_callable=AsyncMock) as lookup:
        lookup.side_effect = [bundle(device_entry()), trace]
        with pytest.raises(ValueError):
            await lookup_self_issuer()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["XHUMA_ODS_CODE", "ORG_ASID"])
async def test_missing_identity_fails_without_sds_request(name, monkeypatch):
    monkeypatch.delenv(name)
    with patch("app.pds.pds.sds_trace", new_callable=AsyncMock) as lookup:
        with pytest.raises(ValueError, match="XHUMA_ODS_CODE and ORG_ASID"):
            await lookup_self_issuer()
        lookup.assert_not_awaited()


def test_self_cache_keys_isolate_partykey_interaction_and_environment(monkeypatch):
    assert sds_cache_key("RRV00", partykey="party-1") != sds_cache_key("RRV00", partykey="party-2")
    assert sds_cache_key("RRV00", True, PARTY_KEY) != sds_cache_key("RRV00", True, PARTY_KEY, None)
    monkeypatch.setattr("app.pds.pds.environment", "int")
    integration_key = sds_cache_key("RRV00")
    monkeypatch.setattr("app.pds.pds.environment", "prod")
    assert sds_cache_key("RRV00") != integration_key


def test_jwt_uses_consumer_endpoint_with_provider_audience():
    token = create_jwt(saml, audience="https://provider.test/fhir", issuer=ISSUER)
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["iss"] == ISSUER
    assert payload["aud"] == "https://provider.test/fhir"
    assert payload["requesting_organization"]["identifier"][0]["value"] == "RRV00"
    assert payload["exp"] - payload["iat"] == 300
    assert jwt.get_unverified_header(token)["alg"] == "none"


@pytest.mark.parametrize("issuer", ["", "  ", None])
def test_jwt_requires_nonempty_issuer(issuer):
    with pytest.raises(ValueError, match="issuer"):
        create_jwt(saml, issuer=issuer)
