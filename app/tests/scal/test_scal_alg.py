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
async def test_GPC_STR_TST_ALG_01():
    """Given the user wishes to view / import all current allergiesOR the system is set to only view / import all current allergies
    When the user selects to access current allergies from GP Connect
    Then the resulting request is populated with valid syntax using the includeAllergies parameter with part parameter includeResolvedAllergies set to false
    And the resulting response is processed successfully by the Consumer."""
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-ALG-01", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_ALG_07():
    """Given I have received a successful, valid allergies message response
    And the response includes an empty active allergies list resource indicating that the patient record has no content recorded
    When I display or use the allergies response
    Then I recognise this as a record with no active allergies recorded
    And I handle it appropriate to my use case and in such a way it is not confused with a clinical assertion of no known allergies
    """
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-ALG-07", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body


@pytest.mark.asyncio
async def test_GPC_STR_TST_ALG_08():
    """Given I have received a successful, valid allergies message response
    And the response includes a single code item which indicates that the clinician has recorded that the patient has no known allergies
    When I display or use the allergies response
    Then I recognise this as a clinical assertion of no known allergies
    And I handle it appropriate to my use case and in such a way it is not confused with an empty list response
    """
    nhsnos = ["9690937375"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-ALG-08", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            code = result.status_code
            body = json.loads(result.body)
            assert "document_id" in body
