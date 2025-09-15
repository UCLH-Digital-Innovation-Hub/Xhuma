import asyncio

import pytest

from app.gpconnect import gpconnect

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_gpconnect():
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

    result = await gpconnect(9690937278, audit_dict)
    assert result["resourceType"] == "Patient"


import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.gpconnect import gpconnect
from app.main import app
from app.tests.configure_tests import get_nhs_ids, load_bundle, load_pds

# client = TestClient(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("nhsno", get_nhs_ids())
@patch("app.pds.pds.lookup_patient")
@patch("app.gpconnect.httpx.post")
@patch("app.gpconnect.redis_client.setex")
# @patch("app.gpconnect.convert_bundle")
@patch("app.gpconnect.base64_xml")
async def test_gpconnect_with_nhs_data(
    mock_base64_xml,
    # mock_convert_bundle,
    mock_redis_setex,
    mock_httpx_post,
    mock_lookup_patient,
    nhsno,
):
    # Load test data
    fake_bundle = load_bundle(nhsno)
    fake_pds = load_pds(nhsno)

    # Mock external dependencies
    mock_lookup_patient.return_value = fake_pds
    mock_httpx_post.return_value = Response(
        status_code=200, content=json.dumps(fake_bundle)
    )
    # mock_convert_bundle.return_value = {"ClinicalDocument": "mocked"}
    mock_base64_xml.return_value = "mocked_base64_doc"

    # Run the test
    result = await gpconnect(nhsno)
    assert "document_id" in result
    # mock_lookup_patient.assert_called_once_with(nhsno)
