"""
Script to run through consumer tests for GP CONNECT Scal
"""

import json

import pytest

from app.gpconnect import gpconnect

from ..log_context import capture_test_logs

audit_dict = {
    "subject_id": "CONE, Stephen",
    "organization": "UCLH - University College London Hospitals - TST",
    "organization_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
    "home_community_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
    "role": {
        "Role": {
            "@codeSystem": "2.16.840.1.113883.6.96",
            "@code": "224608005",
            "@codeSystemName": "SNOMED_CT",
            "@displayName": "Administrative healthcare staff",
            "@xmlns": "urn:hl7-org:v3",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        }
    },
    "purpose_of_use": {
        "PurposeForUse": {
            "@xsi:type": "CE",
            "@code": "TREATMENT",
            "@codeSystem": "2.16.840.1.113883.3.18.7.1",
            "@codeSystemName": "nhin-purpose",
            "@displayName": "Treatment",
            "@xmlns": "urn:hl7-org:v3",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        },
    },
    "resource_id": "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO",
}


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_05():
    """Given I am at a point in the system where I have access to attempt a call to a GP Connect service
    When I make that attempt to access GP Connect
    Then an audit record is written to an appropriate auidit log including when access is blocked, unsuccessful or successful
    And the audit record confirms to NHS Digital audit standards"""
    nhsnos = ["9690937286", "9690938533"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-05", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_06():
    """Given I have access to request data from GP Connect and the patient trace was [time] ago
    When I make that attempt to access GP Connect
    Then the GP Connect is request message is [result]
    Examples

        result: blocked, time: > 24 hours**
        result sent, time: < 24 hours**
    """
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-06", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_07():
    """
        Given I have made a successful request to GP Connect
    When I receive a valid response including a patient resource
    Then I verify the patient resource details for family name, given name, gender, date of birth and GP Practice Code match to those presented to the user from the local system in the patient record
    And I alert the user to any mismatch between the local record demographics and those provided in the GP Connect response message
    """
    nhsnos = []
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-07", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 200
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_08():
    """
        Given I have access to request data from GP Connect and the patient trace was within the last 24 hours
    When I make that attempt to access GP Connect
    Then the registered GP practice from the last PDS trace is used to identify the practice to submit the request to
    """
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-08", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 200
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_09():
    """Given I have access to request data from GP Connect but I cannot confirm the registered practice either because it is not on PDS or the patient has an s-flag
    When I attempt to access GP Connect
    Then the request to GP Connect is blocked and handled gracefully so the user is aware that access is not available for that patient at that time
    """
    nhsnos = ["9690938533", "9690938541"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-09", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 403
            assert body["success"] is False


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_10():
    """
        Given I access a patient which is recorded as deceased on PDS or on the local system
    When I am at a point where I would normally be able to access GP Connect
    Then the system prevents access to GP Connect
    And handles the prevention gracefully so the users is aware that GP Connect is not available for this patient
    """
    nhsnos = ["9690938681"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-10", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 403
            assert body["success"] is False


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_11():
    """Given I have made a request to a GP Connect service
    When I receive a patient not found error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsnos = ["9999999999"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-11", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 404


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_12():
    """Given I have made a request to a GP Connect service
    When I receive a patient dissent to share error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsnos = ["9690938576"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-12", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 403
            assert body["success"] is False


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_13():
    """Given I have made a request to a GP Connect service using an Invalid Resource (The Parameters resource passed does not conform to that specified in the GPConnect-GetStructuredRecord-Operation-1 OperationDefinition)
    When I receive an invalid resource error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-13", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert body["success"] is False


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_14():
    """Given I have made a request to a GP Connect service using an Invalid NHS Number
    When I receive an invalid NHS number error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsnos = ["testno"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-14", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 400
            assert body["success"] is False


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_15():
    """
    Given I have made a request for allergies to a GP Connect service with invalid Allergies Parameters/Part Parameters
    When I receive an invalid parameter error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-15", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            # TODO clarify test as we used fixed allergy parameters
            assert body["success"] is False


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_16():
    """Given I have made a request for medications to a GP Connect service invalid Medications Parametes/Part Parameters
    When I receive an invalid parameter error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-16", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            # TODO clarify test as we used fixed allergy parameters
            assert body["success"] is False


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_17():
    """Given I have sent a valid message to GP Connect
    When I receive a response including a data in transit warning
    Then I make the user aware as appropriate"""
    nhsnos = ["9690938096"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-17", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 200


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_18():
    """Given I have sent a valid message to GP Connect
    And I have requested allergies are included
    When I receive a response including a confidential items warning for allergies
    Then I make the user aware and apply controls as appropriate"""
    nhsnos = ["9690938118"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-18", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 200


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_20():
    """
    Given I have received a valid message response
    When I present the data to the end user
    Then the user is aware that the data has come from the patient's registered GP record (this may be expressed generically or specific to the source practice)
    """
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-20", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert code == 200
