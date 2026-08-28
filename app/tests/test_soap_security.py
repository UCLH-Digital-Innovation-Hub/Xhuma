from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_iti38_rejects_invalid_saml_issuer(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    invalid_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>http://malicious-actor.com</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007"/></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti38",
        content=invalid_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 401
    assert "Invalid SAML Assertion Issuer" in response.text


def test_iti38_accepts_valid_saml_issuer(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    from fastapi import HTTPException

    monkeypatch.setattr(
        "app.soap.soap.process_saml_attributes",
        lambda x: (_ for _ in ()).throw(HTTPException(status_code=200, detail="Success")),
    )
    valid_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer><saml2:AttributeStatement><saml2:Attribute Name="foo"><saml2:AttributeValue>bar</saml2:AttributeValue></saml2:Attribute><saml2:Attribute Name="baz"><saml2:AttributeValue>qux</saml2:AttributeValue></saml2:Attribute></saml2:AttributeStatement></saml2:Assertion></wsse:Security></s:Header><s:Body><query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007"/></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti38",
        content=valid_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert "Invalid SAML Assertion Issuer" not in response.text


def test_iti39_rejects_ssrf_reply_to(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    monkeypatch.setattr("app.soap.soap.client.get", lambda x: b"dummy")
    ssrf_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:wsa="http://www.w3.org/2005/08/addressing"><s:Header><wsa:MessageID>urn:uuid:12345678</wsa:MessageID><wsa:ReplyTo><wsa:Address>http://internal-metadata-server.local/pwned</wsa:Address></wsa:ReplyTo><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><RetrieveDocumentSetRequest><DocumentRequest><DocumentUniqueId>123</DocumentUniqueId></DocumentRequest></RetrieveDocumentSetRequest></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti39",
        content=ssrf_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 400
    assert "ReplyTo must use https" in response.text


def test_iti39_accepts_valid_reply_to(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    monkeypatch.setattr("app.soap.soap.client.get", lambda x: b"dummy")
    valid_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:wsa="http://www.w3.org/2005/08/addressing"><s:Header><wsa:MessageID>urn:uuid:12345678</wsa:MessageID><wsa:ReplyTo><wsa:Address>https://allowed-domain.nhs.uk/callback</wsa:Address></wsa:ReplyTo><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><RetrieveDocumentSetRequest><DocumentRequest><DocumentUniqueId>123</DocumentUniqueId></DocumentRequest></RetrieveDocumentSetRequest></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti39",
        content=valid_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert "ReplyTo must use https" not in response.text
    assert "ReplyTo domain not allowed" not in response.text


def test_iti38_rejects_malformed_xml_no_assertion(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    # This XML is missing the Assertion tag completely, which used to cause an unhandled KeyError
    malformed_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"></wsse:Security></s:Header><s:Body><query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007"/></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti38",
        content=malformed_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 401
    assert "Invalid SAML Assertion Issuer" in response.text


def test_iti38_rejects_multiple_assertions_with_wrong_issuer(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    # This XML has two assertions. If not handled, it parses as a list and throws an AttributeError on .get()
    malformed_soap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>http://wrong</saml2:Issuer></saml2:Assertion><saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"><saml2:Issuer>http://wrong</saml2:Issuer></saml2:Assertion></wsse:Security></s:Header><s:Body><query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007"/></s:Body></s:Envelope>"""
    response = client.post(
        "/SOAP/iti38",
        content=malformed_soap_xml,
        headers={"Content-Type": "application/soap+xml"},
    )
    assert response.status_code == 401
    assert "Invalid SAML Assertion Issuer" in response.text
