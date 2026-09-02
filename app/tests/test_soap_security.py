import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

ENDPOINTS = ["/SOAP/iti38", "/SOAP/iti39", "/SOAP/iti47", "/SOAP/iti55"]


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    monkeypatch.setattr("app.soap.soap.client.get", lambda x: b"dummy")
    monkeypatch.setattr(
        "app.soap.soap.process_saml_attributes",
        lambda x: type(
            "obj",
            (object,),
            {
                "subject_id": "1",
                "organization": "2",
                "organization_id": "3",
                "role": "4",
            },
        )(),
    )
    monkeypatch.setattr(
        "app.soap.soap.lookup_patient", lambda *args, **kwargs: {"id": "test"}
    )

    # mock the responses so they don't fail later in the pipeline
    async def mock_response(*args, **kwargs):
        return b"success"

    monkeypatch.setattr("app.soap.soap.iti_38_response", mock_response)
    monkeypatch.setattr("app.soap.soap.iti_39_response", mock_response)
    monkeypatch.setattr("app.soap.soap.iti_47_response", mock_response)
    monkeypatch.setattr("app.soap.soap.iti_55_response", mock_response)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_rejects_invalid_saml_issuer(endpoint):
    invalid_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>http://malicious-actor.com</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007"/></s:Body></s:Envelope>"""
    response = client.post(
        endpoint,
        content=invalid_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 401
    assert "Untrusted SAML issuer" in response.text


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_rejects_malformed_xml_no_assertion(endpoint):
    malformed_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"></wsse:Security></s:Header><s:Body><query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007"/></s:Body></s:Envelope>"""
    response = client.post(
        endpoint,
        content=malformed_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 401
    assert "Missing SAML assertion" in response.text


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_rejects_multiple_assertions(endpoint):
    malformed_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer></saml2:Assertion><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007"/></s:Body></s:Envelope>"""
    response = client.post(
        endpoint,
        content=malformed_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 401
    assert "Expected exactly one SAML assertion" in response.text


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_xxe_malicious_entity_rejected(endpoint):
    xxe_soap_xml = """<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE foo [
<!ELEMENT foo ANY >
<!ENTITY xxe SYSTEM "file:///etc/passwd" >]>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>&xxe;</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body></s:Body></s:Envelope>"""
    response = client.post(
        endpoint, content=xxe_soap_xml, headers={"Content-Type": "application/soap+xml"}
    )
    # defusedxml should raise an EntitiesForbidden error which fastapi catches as a 400 Bad Request or 500
    assert response.status_code in [400, 500]


def test_iti39_rejects_ssrf_reply_to():
    ssrf_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:wsa="http://www.w3.org/2005/08/addressing"><s:Header><wsa:MessageID>urn:uuid:12345678</wsa:MessageID><wsa:ReplyTo><wsa:Address>http://internal-metadata-server.local/pwned</wsa:Address></wsa:ReplyTo><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><RetrieveDocumentSetRequest><DocumentRequest><DocumentUniqueId>123</DocumentUniqueId></DocumentRequest></RetrieveDocumentSetRequest></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti39",
        content=ssrf_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 400
    assert "ReplyTo must use https" in response.text


def test_iti39_accepts_valid_reply_to():
    valid_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:wsa="http://www.w3.org/2005/08/addressing"><s:Header><wsa:MessageID>urn:uuid:12345678</wsa:MessageID><wsa:ReplyTo><wsa:Address>https://allowed-domain.nhs.uk/callback</wsa:Address></wsa:ReplyTo><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><RetrieveDocumentSetRequest><DocumentRequest><DocumentUniqueId>123</DocumentUniqueId></DocumentRequest></RetrieveDocumentSetRequest></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti39",
        content=valid_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert "ReplyTo must use https" not in response.text
    assert "ReplyTo domain not allowed" not in response.text
