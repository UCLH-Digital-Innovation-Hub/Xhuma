from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

mock_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:wsa="http://www.w3.org/2005/08/addressing">
  <s:Header>
    <wsa:MessageID>urn:uuid:pentest-67890</wsa:MessageID>
    <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
        <saml2:Issuer>urn:nhs:names:services:spine</saml2:Issuer>
      </saml2:Assertion>
    </wsse:Security>
  </s:Header>
  <s:Body>
    <query:CrossGatewayQuery xmlns:query="urn:ihe:iti:xds-b:2007">
      <query:AdhocQuery id="urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d">
        <query:Slot name="$XDSDocumentEntryPatientId">
          <query:ValueList>
            <query:Value>'9658218873^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO'</query:Value>
          </query:ValueList>
        </query:Slot>
      </query:AdhocQuery>
    </query:CrossGatewayQuery>
  </s:Body>
</s:Envelope>"""

response = client.post(
    "/SOAP/iti38",
    content=mock_xml,
    headers={
        "Content-Type": 'application/soap+xml; action="urn:ihe:iti:2007:CrossGatewayQuery"'
    },
)
print("STATUS:", response.status_code)
print("BODY:", response.text)
