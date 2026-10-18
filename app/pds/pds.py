import asyncio
import hashlib
import hmac
import json
import logging
import os
import pprint
import uuid
from urllib.parse import urlsplit

import fastapi
import httpx

from app.audit.audit import AuditFailureException, attempt_audit, process_saml_attributes
from app.audit.models import AuditOutcome, SAMLAttributes
from app.logging import log_request, log_response
from app.redis_connect import redis_client
from app.security import pds_jwt
from app.telemetry import measure, record_cache

DEV_BASE_PATH = "https://dev.api.service.nhs.uk/"
INT_BASE_PATH = "https://int.api.service.nhs.uk/"
PROD_BASE_PATH = "https://api.service.nhs.uk/"
API_KEY = os.getenv("API_KEY")
PDS_CACHE_HOURS = int(os.getenv("PDS_CACHE_HOURS", 24))
SDS_CACHE_HOURS = int(os.getenv("SDS_CACHE_HOURS", 12))
GP_CONNECT_INTERACTION_ID = "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1"
SDS_TRANSIENT_STATUS_CODES = {408, 500, 502, 503, 504}
SDS_RETRY_DELAYS = (0.25, 0.50)
SDS_MAX_ATTEMPTS = 3


class SDSUpstreamError(Exception):
    """Safe structured exception for SDS upstream lookup failures."""

    def __init__(
        self,
        status_code: int | None = None,
        attempts: int = 1,
        resource_type: str = "Device",
        reason: str = "upstream_http_failure",
    ):
        self.status_code = status_code
        self.attempts = attempts
        self.resource_type = resource_type
        self.reason = reason
        super().__init__(
            f"SDS {resource_type} upstream failure (status={status_code}, attempts={attempts}, reason={reason})"
        )

    def __repr__(self) -> str:
        return (
            f"SDSUpstreamError(status_code={self.status_code}, "
            f"attempts={self.attempts}, "
            f"resource_type='{self.resource_type}', "
            f"reason='{self.reason}')"
        )


def _sanitize_diagnostic(text: str) -> str:
    """Safely bounds and sanitizes diagnostic strings from upstream responses."""
    if not isinstance(text, str):
        text = str(text)
    import re

    # Redact potential NHS numbers (10 consecutive digits)
    text = re.sub(r"\b\d{10}\b", "[REDACTED_NHSNO]", text)
    # Redact potential Bearer tokens and JWTs
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9\-\._~+\/]+", "Bearer [REDACTED_TOKEN]", text)
    # Redact UUID-like API keys
    text = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "[REDACTED_UUID]", text
    )

    return text.replace("\n", " ").replace("\r", "")[:200]


# router = fastapi.APIRouter(prefix="/pds")

# use environment to set path
environment = os.getenv("ENV", "dev").lower()
if environment == "dev":
    BASE_PATH = DEV_BASE_PATH
elif environment == "int":
    BASE_PATH = INT_BASE_PATH
elif environment == "prod":
    BASE_PATH = PROD_BASE_PATH
else:
    raise ValueError(f"Unknown or unsupported environment: {environment}")


def pds_cache_key(nhsno: int, secret: str = None) -> str:
    """Return a deterministic, pseudonymous Redis key for a patient lookup."""
    secret = secret or os.getenv("PDS_CACHE_HMAC_SECRET") or API_KEY
    digest = hmac.new(secret.encode("utf-8"), str(nhsno).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"pds:patient:{digest}"


def sds_cache_key(
    ods: str,
    endpoint: bool = False,
    partykey: str = None,
    interaction_id: str | None = GP_CONNECT_INTERACTION_ID,
    manufacturer_ods: str = None,
) -> str:
    """Return the deterministic Redis key for an SDS query."""
    resource = "endpoint" if endpoint else "device"
    key = f"pds:sds:{environment}:{resource}:{str(ods).upper()}"
    if partykey:
        key = f"{key}:{partykey}"
    if manufacturer_ods:
        key = f"{key}:manufacturer:{str(manufacturer_ods).upper()}"
    if interaction_id != GP_CONNECT_INTERACTION_ID:
        key = f"{key}:interaction:{interaction_id or 'all'}"
    return key


# @router.get("/lookup_patient/{nhsno}")
async def lookup_patient(nhsno: int, request: fastapi.Request = None, saml: SAMLAttributes = None):
    if not saml:
        raise ValueError("Missing SAML attributes: Clinical lookup cannot continue unaudited.")

    cache_key = pds_cache_key(nhsno)
    with measure("pds.cache.read") as span:
        cached_patient = await redis_client.get(cache_key)
        span.set_attribute("cache.hit", bool(cached_patient))
    record_cache("pds", bool(cached_patient))
    if cached_patient:
        logging.info("Cache hit for PDS patient query")
        if isinstance(cached_patient, bytes):
            cached_patient = cached_patient.decode("utf-8")

        patient_dict = json.loads(cached_patient)
        outcome = (
            AuditOutcome.fail
            if ("resourceType" in patient_dict and patient_dict["resourceType"] == "OperationOutcome")
            else AuditOutcome.ok
        )
        await attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml,
            action="pds_lookup",
            outcome=outcome,
            detail={"cache_hit": True},
        )
        return patient_dict

    logging.info("Cache miss for PDS patient query. Fetching from PDS API.")

    async def get_pds_token():
        full_path = f"{BASE_PATH}oauth2/token"
        nhs_api_key = os.getenv("NHS_API_KEY", "").strip()
        if not nhs_api_key:
            raise ValueError("NHS_API_KEY is not configured")
        jwt_token = await asyncio.to_thread(pds_jwt, nhs_api_key, nhs_api_key, full_path)
        # print(f"jwt_token: {jwt_token}")

        oauth_params = {
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": jwt_token,
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(full_path, data=oauth_params)

        try:
            response_dict = r.json()
        except Exception:
            response_dict = {}

        if r.status_code != 200 or "access_token" not in response_dict:
            err_code = (
                response_dict.get("issue", [{}])[0].get("details", {}).get("coding", [{}])[0].get("code", "unknown")
            )
            err_desc = _sanitize_diagnostic(response_dict.get("issue", [{}])[0].get("diagnostics", "Unknown error"))

            if "error" in response_dict:
                err_code = str(response_dict.get("error"))[:50]
                err_desc = _sanitize_diagnostic(response_dict.get("error_description", err_desc))

            req_id = r.headers.get("x-request-id", "")
            corr_id = r.headers.get("x-correlation-id", "")

            safe_log = (
                f"PDS OAuth Failure | Status: {r.status_code} | "
                f"Code: {err_code} | Desc: {err_desc} | "
                f"ReqID: {req_id} | CorrID: {corr_id}"
            )
            logging.error(safe_log)

            if r.status_code in (401, 403):
                raise fastapi.HTTPException(status_code=502, detail="NHS API Authentication or Entitlement Failed")
            raise fastapi.HTTPException(status_code=502, detail="Upstream NHS API Availability Failure")

        nhs_token = response_dict["access_token"]

        await redis_client.setex("access_token", response_dict["expires_in"], nhs_token)
        return nhs_token

    # if nhs token expired or not request, get one and cache

    try:
        cached_token = await redis_client.get("access_token")
        if not cached_token:
            logging.info("NHS token expired or not found, getting new one")
            nhs_token = await get_pds_token()
        else:
            logging.info("NHS token found in cache")
            nhs_token = cached_token.decode("utf-8") if isinstance(cached_token, bytes) else cached_token
    except AuditFailureException:
        raise
    except fastapi.HTTPException as e:
        await attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml,
            action="pds_token_fetch",
            outcome=AuditOutcome.fail,
            detail={"exception": e.detail},
            error_code=str(e.status_code),
        )
        raise
    except Exception as e:
        await attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml,
            action="pds_token_fetch",
            outcome=AuditOutcome.fail,
            detail={"exception": type(e).__name__},
            error_code="502",
        )
        raise fastapi.HTTPException(status_code=502, detail="Failed to obtain PDS token due to upstream error")

    # print(f"nhs_token: {nhs_token}")
    # set headers for pds request
    headers = {
        "X-Request-ID": str(uuid.uuid4()),
        "X-Correlation-ID": str(uuid.uuid4()),
        "NHSD-End-User-Organisation-ODS": os.environ["ORG_CODE"],
        "Authorization": f"Bearer {nhs_token}",
        "accept": "application/fhir+json",
    }

    url = f"{BASE_PATH}personal-demographics/FHIR/R4/Patient/{nhsno}"
    try:
        async with httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                error_data = {}
                try:
                    error_data = r.json()
                except Exception:
                    pass

                err_code = (
                    error_data.get("issue", [{}])[0].get("details", {}).get("coding", [{}])[0].get("code", "unknown")
                )
                err_desc = _sanitize_diagnostic(error_data.get("issue", [{}])[0].get("diagnostics", "Unknown error"))

                if "error" in error_data:
                    err_code = str(error_data.get("error"))[:50]
                    err_desc = _sanitize_diagnostic(error_data.get("error_description", err_desc))

                req_id = r.headers.get("x-request-id", "")
                corr_id = r.headers.get("x-correlation-id", "")

                safe_log = (
                    f"PDS API Failure | Status: {r.status_code} | "
                    f"Code: {err_code} | Desc: {err_desc} | "
                    f"ReqID: {req_id} | CorrID: {corr_id}"
                )
                logging.error(safe_log)

                if r.status_code == 404:
                    raise fastapi.HTTPException(status_code=404, detail="Patient not found on PDS")
                if r.status_code in (401, 403):
                    raise fastapi.HTTPException(status_code=502, detail="PDS Entitlement or Token Failure")
                raise fastapi.HTTPException(status_code=502, detail="PDS lookup upstream failure")

            patient_dict = r.json()
    except AuditFailureException:
        raise
    except fastapi.HTTPException as e:
        await attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml,
            action="pds_lookup",
            outcome=AuditOutcome.fail,
            detail={"cache_hit": False, "status_code": e.status_code, "detail": e.detail},
            error_code=str(e.status_code),
        )
        raise
    except Exception as e:
        await attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml,
            action="pds_lookup",
            outcome=AuditOutcome.fail,
            detail={"cache_hit": False, "exception": type(e).__name__},
            error_code="502",
        )
        raise fastapi.HTTPException(status_code=502, detail="PDS lookup upstream failure")

    outcome = (
        AuditOutcome.fail
        if ("resourceType" in patient_dict and patient_dict["resourceType"] == "OperationOutcome")
        else AuditOutcome.ok
    )
    await attempt_audit(
        request=request,
        nhs_number=str(nhsno),
        saml=saml,
        action="pds_lookup",
        outcome=outcome,
        detail={"cache_hit": False, "status_code": r.status_code},
    )

    await redis_client.setex(cache_key, PDS_CACHE_HOURS * 60 * 60, json.dumps(patient_dict))
    return patient_dict


# @router.get("/sds/{ods}")
async def sds_trace(ods: str, endpoint: bool = False, **kwargs):
    """
    Function to get the SDS trace for an ODS code

    args:
    ods: str - the ODS code to trace
    endpoint: bool - whether to make an endpoint SDS trace
    mhsparty: str - optional MHS Party Key to narrow the Device or Endpoint search
    interaction_id: str - defaults to getstructuredrecord; None allows an Endpoint search by ODS and Party Key only

    returns:
    fhir bundle of the SDS trace
    """
    partykey = kwargs.get("mhsparty")
    manufacturer_ods = kwargs.get("manufacturer_ods")
    interaction_id = kwargs.get("interaction_id", GP_CONNECT_INTERACTION_ID)
    if not interaction_id and (not endpoint or not partykey):
        raise ValueError("An SDS search without an interaction ID requires an Endpoint and MHS Party Key")
    cache_key = sds_cache_key(
        ods,
        endpoint=endpoint,
        partykey=partykey,
        interaction_id=interaction_id,
        manufacturer_ods=manufacturer_ods,
    )
    with measure("sds.cache.read") as span:
        cached_trace = await redis_client.get(cache_key)
        span.set_attribute("cache.hit", bool(cached_trace))
    record_cache("sds", bool(cached_trace))
    if cached_trace:
        logging.info("Cache hit for SDS query %s", cache_key)
        if isinstance(cached_trace, bytes):
            cached_trace = cached_trace.decode("utf-8")
        return json.loads(cached_trace)

    logging.info("Cache miss for SDS query %s. Fetching from SDS API.", cache_key)

    suffix = "Endpoint" if endpoint else "Device"
    identifier = []
    if interaction_id:
        identifier.append(f"https://fhir.nhs.uk/Id/nhsServiceInteractionId|{interaction_id}")
    if partykey:
        identifier.append(f"https://fhir.nhs.uk/Id/nhsMhsPartyKey|{partykey}")

    url = f"{BASE_PATH}spine-directory/FHIR/R4/{suffix}"
    organisation = f"https://fhir.nhs.uk/Id/ods-organization-code|{ods}"

    nhs_api_key = os.getenv("NHS_API_KEY", "").strip()
    if not nhs_api_key:
        raise ValueError("NHS_API_KEY environment variable is not set")
    parameters = {
        "organization": organisation,
        "identifier": identifier,
    }
    if manufacturer_ods:
        parameters["manufacturing-organization"] = (
            f"https://fhir.nhs.uk/Id/ods-organization-code|{manufacturer_ods.strip().upper()}"
        )
    # print(parameters)
    headers = {
        "accept": "application/fhir+json",
        "apikey": nhs_api_key,
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(1, SDS_MAX_ATTEMPTS + 1):
            request_headers = {
                **headers,
                "X-Request-ID": str(uuid.uuid4()),
            }
            try:
                with measure("sds.http", resource_type=suffix, attempt=attempt) as span:
                    r = await client.get(url, headers=request_headers, params=parameters)
                    span.set_attribute("http_status_code", r.status_code)
                    span.set_attribute("http_success", r.status_code == 200)
            except httpx.TransportError as exc:
                if attempt < SDS_MAX_ATTEMPTS:
                    delay = SDS_RETRY_DELAYS[attempt - 1]
                    logging.warning(
                        "SDS %s lookup transient transport error on attempt %d: %s. Retrying...",
                        suffix,
                        attempt,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise SDSUpstreamError(
                    status_code=None,
                    attempts=attempt,
                    resource_type=suffix,
                    reason="transport_error",
                ) from None

            if r.status_code == 200:
                try:
                    trace = json.loads(r.text)
                except Exception:
                    raise SDSUpstreamError(
                        status_code=r.status_code,
                        attempts=attempt,
                        resource_type=suffix,
                        reason="malformed_json",
                    ) from None
                await redis_client.setex(cache_key, SDS_CACHE_HOURS * 60 * 60, json.dumps(trace))
                return trace

            if r.status_code in SDS_TRANSIENT_STATUS_CODES and attempt < SDS_MAX_ATTEMPTS:
                delay = SDS_RETRY_DELAYS[attempt - 1]
                logging.warning(
                    "SDS %s lookup transient HTTP %s on attempt %d. Retrying...",
                    suffix,
                    r.status_code,
                    attempt,
                )
                await asyncio.sleep(delay)
                continue

            raise SDSUpstreamError(
                status_code=r.status_code,
                attempts=attempt,
                resource_type=suffix,
                reason="upstream_http_failure",
            )


async def lookup_self_device() -> dict:
    """Resolve Xhuma's SDS consumer Device from the care organisation and manufacturer."""
    manufacturer_ods = os.getenv("XHUMA_ODS_CODE", "").strip().upper()
    consumer_ods = os.getenv("ORG_CODE", "").strip().upper()
    partykey = os.getenv("XHUMA_MHS_PARTY_KEY", "").strip()
    asid = os.getenv("ORG_ASID", "").strip()
    if not manufacturer_ods or not consumer_ods or not asid:
        raise ValueError("XHUMA_ODS_CODE, ORG_CODE and ORG_ASID are required for the SDS self lookup")

    trace = await sds_trace(consumer_ods, manufacturer_ods=manufacturer_ods)
    if trace.get("resourceType") != "Bundle":
        raise ValueError("SDS self lookup did not return a Bundle")

    matches = []
    for entry in trace.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Device":
            continue
        identifiers = resource.get("identifier", [])
        if any(
            item.get("system") == "https://fhir.nhs.uk/Id/nhsSpineASID" and item.get("value") == asid
            for item in identifiers
        ) and (
            not partykey
            or any(
                item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey" and item.get("value") == partykey
                for item in identifiers
            )
        ):
            matches.append(entry)

    if len(matches) != 1:
        raise ValueError("SDS self lookup must return exactly one Device matching the configured Spine identity")
    return matches[0]


async def lookup_self_issuer() -> str:
    """Return Xhuma's Spine Endpoint address for the GP Connect JWT iss claim."""
    device_entry = await lookup_self_device()
    partykeys = {
        item["value"]
        for item in device_entry["resource"].get("identifier", [])
        if item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey" and item.get("value")
    }
    if len(partykeys) != 1:
        raise ValueError("SDS self Device must contain exactly one MHS Party Key")
    partykey = partykeys.pop()
    # Consumer routing records need not advertise the provider's getstructuredrecord interaction.
    trace = await sds_trace(
        os.environ["XHUMA_ODS_CODE"].strip().upper(), endpoint=True, mhsparty=partykey, interaction_id=None
    )
    if trace.get("resourceType") != "Bundle":
        raise ValueError("SDS self Endpoint lookup did not return a Bundle")

    addresses = set()
    for entry in trace.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Endpoint" or resource.get("status") != "active":
            continue
        if not any(
            item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey" and item.get("value") == partykey
            for item in resource.get("identifier", [])
        ):
            continue
        address = resource.get("address", "")
        parsed = urlsplit(address)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("SDS self Endpoint must contain an absolute HTTP(S) address")
        addresses.add(address)
    if len(addresses) != 1:
        raise ValueError("SDS self lookup must return exactly one consumer Spine Endpoint address")
    return addresses.pop()


if __name__ == "__main__":
    import xmltodict

    from app.db import make_engine, make_sessionmaker

    xml38 = '<AttributeStatement><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:subject-id"><AttributeValue>CONE, Stephen</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization"><AttributeValue>UCLH - University College London Hospitals - TST</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization-id"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:nhin:names:saml:homeCommunityId"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><AttributeValue><Role xsi:type="CE" code="224608005" codeSystem="2.16.840.1.113883.6.96" codeSystemName="SNOMED_CT" displayName="Administrative healthcare staff" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"><AttributeValue><PurposeForUse xsi:type="CE" code="TREATMENT" codeSystem="2.16.840.1.113883.3.18.7.1" codeSystemName="nhin-purpose" displayName="Treatment" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:resource:resource-id"><AttributeValue>9692136744^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</AttributeValue></Attribute></AttributeStatement>'
    saml = process_saml_attributes(xmltodict.parse(xml38)["AttributeStatement"])

    async def main():
        engine = make_engine()
        try:
            app = fastapi.FastAPI()
            app.state.SessionLocal = make_sessionmaker(engine)

            request = fastapi.Request(
                {
                    "type": "http",
                    "app": app,
                    "headers": [],
                }
            )

            issuer = await lookup_self_issuer()
            print(f"GP Connect issuer: {issuer}")

            return await lookup_patient(
                9658218873,
                request=request,
                saml=saml,
            )
        finally:
            await engine.dispose()
            await redis_client.close()

    pprint.pprint(asyncio.run(main()))

    # print(patient.gender)
    # print(patient.name[0].family)
    # print(patient.generalPractitioner[0].identifier.value)

    # ods = asyncio.run(sds_trace("A82038"))
    # pprint.pprint(ods)
    # for i in ods["entry"]:
    #     pprint.pprint(i)

    # try self lookup
    prefix = "https://fhir.nhs.uk/Id/"
    "https://fhir.nhs.uk/Id/objectClass|nhsAs"
    parameters = {
        "nhsIDCode": "RVV00",
        "objectClass": "nhsAs",
        "nhsAsSvcIAD": "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
        "nhsMhsManufacturerOrg": "RRV00",
    }
    indentifiers = [
        f"{prefix}nhsIDCode|RVV00",
        f"{prefix}objectClass|nhsAs",
    ]
    # url = f"{INT_BASE_PATH}spine-directory/FHIR/R4/{suffix}"
    # r = httpx.get(url, headers=headers, params=parameters)
