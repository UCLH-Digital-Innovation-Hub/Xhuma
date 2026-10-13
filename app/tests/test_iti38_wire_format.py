import json
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from app.audit.models import SAMLAttributes
from app.ccda.models.datatypes import CD
from app.soap.responses.constants import COMMUNITY_ID
from app.soap.responses.iti_38 import iti_38_response


@pytest.fixture
def mock_saml():
    return SAMLAttributes(
        role=CD(code="test-role"),
        organization="test-org",
        purpose_of_use=CD(code="test-pou"),
    )


@pytest.fixture
def mock_request():
    request = MagicMock()
    request.headers = {}
    return request


@pytest.mark.asyncio
async def test_iti38_failure_wire_format(mock_request, mock_saml):
    # Simulate a GP Connect failure
    with (
        patch("app.soap.responses.iti_38.redis_client.get", return_value=None),
        patch(
            "app.soap.responses.iti_38.gpconnect",
            return_value=MagicMock(body=json.dumps({"success": False, "error": "Simulated GP Connect failure"})),
        ),
        patch("app.soap.responses.iti_38.attempt_audit"),
    ):
        xml_str = await iti_38_response(mock_request, 1234567890, "ceid", "queryid", mock_saml)

        root = ET.fromstring(xml_str)
        # s:Envelope / s:Body / AdhocQueryResponse
        namespaces = {
            "s": "http://www.w3.org/2003/05/soap-envelope",
            "query": "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0",
            "rs": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
        }

        body = root.find("s:Body", namespaces)
        assert body is not None

        response = body.find("query:AdhocQueryResponse", namespaces)
        assert response is not None

        assert response.attrib.get("status") == "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"

        error_list = response.find("rs:RegistryErrorList", namespaces)
        assert error_list is not None

        error = error_list.find("rs:RegistryError", namespaces)
        assert error is not None

        assert error.attrib.get("errorCode") == "XDSRegistryError"
        assert error.attrib.get("codeContext") == "Simulated GP Connect failure"
        assert error.attrib.get("severity") == "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error"
        assert error.attrib.get("location") == f"urn:oid:{COMMUNITY_ID}"


@pytest.mark.asyncio
async def test_iti38_success_wire_format(mock_request, mock_saml):
    # Simulate a success cache hit
    with (
        patch("app.soap.responses.iti_38.redis_client.get", return_value=b"test-doc-id"),
        patch("app.soap.responses.iti_38.attempt_audit"),
    ):
        xml_str = await iti_38_response(mock_request, 1234567890, "ceid", "queryid", mock_saml)

        root = ET.fromstring(xml_str)
        namespaces = {
            "s": "http://www.w3.org/2003/05/soap-envelope",
            "query": "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0",
            "rim": "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0",
        }

        body = root.find("s:Body", namespaces)
        assert body is not None

        response = body.find("query:AdhocQueryResponse", namespaces)
        assert response is not None
        assert response.attrib.get("status") == "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"

        # Should not have RegistryErrorList
        assert response.find("rs:RegistryErrorList", {"rs": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0"}) is None

        obj_list = response.find("rim:RegistryObjectList", namespaces)
        assert obj_list is not None

        extrinsic = obj_list.find("rim:ExtrinsicObject", namespaces)
        assert extrinsic is not None
        assert extrinsic.attrib.get("id") == "test-doc-id"
