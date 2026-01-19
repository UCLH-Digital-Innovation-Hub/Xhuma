"""
Script to run through consumer tests for GP CONNECT Scal
"""

import json
import os

import pytest

from app.gpconnect import gpconnect
from app.pds.pds import lookup_patient, sds_trace
from app.tests.log_context import capture_test_logs

from .test_scal_alg import audit_dict


@pytest.mark.asyncio
async def test_GPC_SPN_TST_01():
    """Given I am using the Spine Directory Services LDAP server And I am performing the ldapsearch to retrieve the ASID and the MHS Party Key
    When I request the ldapsearch operation
    Then I set Accredited System type as nhsAS, Organisation code as the GP Practice Organisation code and the InteractionID as a specific GP Connect Interaction ID
    And the ldap response should contain the ASID and the MHS Party Key of the GP Practice Organisation Code
    """
    nhsnos = [9658218873]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-SPN-TST-01", nhsno) as log_dir:

            with open(os.path.join(log_dir, "GPC-SPN-TST-01.log"), "a") as f:
                pds_search = await lookup_patient(nhsno)
                f.write(f"PDS trace output" + json.dumps(pds_search) + "\n")
                gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]
                f.write(f"GP ODS: {gp_ods}\n")
                # sds_response = await sds_trace(gp_ods, endpoint=False)
                asid_trace = await sds_trace(gp_ods)
                f.write(f"SDS ASID trace output" + json.dumps(asid_trace) + "\n")

                asid = None
                nhsmhsparty = None
                for item in (
                    asid_trace.get("entry", [{}])[0]
                    .get("resource", {})
                    .get("identifier", [])
                ):
                    if item.get("system") == "https://fhir.nhs.uk/Id/nhsSpineASID":
                        asid = item.get("value")
                    elif item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey":
                        nhsmhsparty = item.get("value")
                f.write(f"ASID: {asid}\n")
                f.write(f"NHS MHS Party Key: {nhsmhsparty}\n")
                assert asid == "200000000985"
                assert nhsmhsparty == "A20047-821870"


@pytest.mark.asyncio
async def test_GPC_SPN_TST_02():
    """Given I am using the default Spine Directory Services LDAP server
    And I am performing the ldapsearch to retrieve the FHIR endpoint URL of the MHS
    When I request the ldapsearch operation
    Then I set Message Handling System type as nhsMHS, provide the MHS Party Key and the Interaction ID as a specific GP Connect Interaction ID
    And the ldap response should contain the FHIR endpoint URL of the MHS in the nhsMhsEndPoint
    """
    nhsnos = [9658218873]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-SPN-TST-02", nhsno) as log_dir:

            with open(os.path.join(log_dir, "GPC-SPN-TST-02.log"), "a") as f:
                pds_search = await lookup_patient(nhsno)
                f.write(f"PDS trace output" + json.dumps(pds_search) + "\n")
                gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]
                f.write(f"GP ODS: {gp_ods}\n")
                # sds_response = await sds_trace(gp_ods, endpoint=False)
                asid_trace = await sds_trace(gp_ods)
                f.write(f"SDS ASID trace output" + json.dumps(asid_trace) + "\n")

                asid = None
                nhsmhsparty = None
                for item in (
                    asid_trace.get("entry", [{}])[0]
                    .get("resource", {})
                    .get("identifier", [])
                ):
                    if item.get("system") == "https://fhir.nhs.uk/Id/nhsSpineASID":
                        asid = item.get("value")
                    elif item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey":
                        nhsmhsparty = item.get("value")
                f.write(f"ASID: {asid}\n")
                f.write(f"NHS MHS Party Key: {nhsmhsparty}\n")
                endpoint_trace = await sds_trace(
                    gp_ods, endpoint=True, mhsparty=nhsmhsparty
                )
                f.write(
                    f"SDS Endpoint trace output" + json.dumps(endpoint_trace) + "\n"
                )
                fhir_endpoint_url = endpoint_trace["entry"][0]["resource"]["address"]
                f.write(f"FHIR Endpoint URL: {fhir_endpoint_url}\n")
                assert fhir_endpoint_url is not None


@pytest.mark.asyncio
async def test_GPC_SPN_TST_03():
    """Given I am using the default server
    And I am performing a Foundations, Appointments, HTML GetCareRecord OR GetStructuredRecord interaction interaction
    When I make a GPConnect request
    Then I use a valid client certificate
    And the response status code should indicate success"""
    nhsnos = [9658218873]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-SPN-TST-03", nhsno) as log_dir:

            with open(os.path.join(log_dir, "GPC-SPN-TST-03.log"), "a") as f:
                result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
                f.write(f"GP Connect response status code: {result.status_code}\n")
                code = result.status_code
                f.write(f"Response code: {code}\n")
                assert code == 200


# GPC-SPN-TST-04 ciphers
# GPC-SPN-TST-05
# not sending xml


@pytest.mark.asyncio
async def test_GPC_SPN_TST_06():
    """Given I am using the default serverAnd I am performing a Foundations, Appointments, HTML GetCareRecord or GetStructuredRecord interaction
    And I set the request content type to ""application/json+fhir"
    And I do not send header ""Accept"
    And I add the parameter ""_format"" with the value ""application/json+fhir"
    When I make the GPConnect request
    Then the response status code should indicate success
    And the response body should be FHIR JSON"""
    nhsnos = [9658218873]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-SPN-TST-06", nhsno) as log_dir:

            with open(os.path.join(log_dir, "GPC-SPN-TST-06.log"), "a") as f:
                result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
                f.write(f"GP Connect response status code: {result.status_code}\n")
                code = result.status_code
                f.write(f"Response code: {code}\n")
                assert code == 200


@pytest.mark.asyncio
async def test_GPC_SPN_TST_07():
    """Given I am using the default server
    And I am performing a Foundations, Appointments, HTML GetCareRecord or GetStructuredRecord interaction
    And I author a GPConnect request for patient with NHS Number (NHS Number to be provided by NHS Digital) who does not consent to their record being shared
    When I make a valid request
    Then the response status code should be ""403"" and Error Code of "NO_PATIENT_CONSENT"
    And I will display appropriate error response
    And I will add the interaction to the audit trail"""
    nhsnos = [9450056234]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-SPN-TST-07", nhsno) as log_dir:

            with open(os.path.join(log_dir, "GPC-SPN-TST-07.log"), "a") as f:
                result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
                f.write(f"GP Connect response status code: {result.status_code}\n")
                code = result.status_code
                f.write(f"Response code: {code}\n")
                assert code == 403


@pytest.mark.asyncio
async def test_GPC_SPN_TST_11():
    """Given I am using the default server
    And I am performing a Foundations, Appointments, HTML GetCareRecord or GetStructuredRecord interaction
    And I author a GPConnect request for patient with NHS Number (NHS Number to be provided by NHS Digital) who does not consent to their record being shared
    When I make a valid request
    Then the response status code should be ""403"" and Error Code of "NO_PATIENT_CONSENT"
    And I will display appropriate error response
    And I will add the interaction to the audit trail"""
    nhsnos = [9658218873]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-SPN-TST-11", nhsno) as log_dir:

            with open(os.path.join(log_dir, "GPC-SPN-TST-11.log"), "a") as f:
                result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
                f.write(f"GP Connect response status code: {result.status_code}\n")
                code = result.status_code
                f.write(f"Response code: {code}\n")
                assert code == 200


@pytest.mark.asyncio
async def test_GPC_SPN_TST_13():
    """Given I am using the default server
    And I am performing a Foundations, Appointments, HTML GetCareRecord or GetStructuredRecord interaction
    And I author a GPConnect request for patient with NHS Number (NHS Number to be provided by NHS Digital) who does not consent to their record being shared
    When I make a valid request
    Then the response status code should be ""403"" and Error Code of "NO_PATIENT_CONSENT"
    And I will display appropriate error response
    And I will add the interaction to the audit trail"""
    nhsnos = [9658218873]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-SPN-TST-13", nhsno) as log_dir:

            with open(os.path.join(log_dir, "GPC-SPN-TST-13.log"), "a") as f:
                result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
                f.write(f"GP Connect response status code: {result.status_code}\n")
                code = result.status_code
                f.write(f"Response code: {code}\n")
                assert code == 403
