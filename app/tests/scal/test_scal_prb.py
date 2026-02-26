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
async def test_GPC_STR_TST_PRB_01():
    """Given the user wishes to view / import all problems or the system is set to only view / import all problems
    When the user selects to problems
    Then the resulting request is populated with valid syntax of using the includeProblems parameter only
    And the resulting response is processed successfully by the Consumer."""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-PRB-01", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_PRB_04():
    """Given GIVEN the user wishes to view / import all problems or the system is set to only view / import all problems
    When the user selects to problems
    Then the resulting request is populated with valid syntax of using the includeProblems parameter only
    And the resulting response is processed successfully by the Consumer, confirming the reason why there is no data
    """
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-PRB-04", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_PRB_05():
    """Given I have made a request for problems to a GP Connect service invalid Problems Parametes/Part Parameters
    When I receive an invalid parameter error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-PRB-05", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_PRB_08():
    """Given I have sent a valid request for problems
    When the provider returns a success response
    Then I handle the response gracefully
    And inform users appropriately according to my use case"""
    nhsnos = ["9658218873"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-PRB-08", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body
