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
