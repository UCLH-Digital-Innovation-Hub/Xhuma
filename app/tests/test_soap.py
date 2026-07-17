from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
import pytest

client = TestClient(app)

MOCK_ITI55_REQUEST = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:urn="urn:hl7-org:v3">
   <soap:Header>
      <urn:MessageID>12345</urn:MessageID>
   </soap:Header>
   <soap:Body>
      <urn:PRPA_IN201305UV02>
         <urn:controlActProcess>
            <urn:queryByParameter>
                <urn:parameterList>
                    <urn:livingSubjectId>
                        <urn:value root="2.16.840.1.113883.2.1.4.1" extension="9449305452"/>
                    </urn:livingSubjectId>
                </urn:parameterList>
            </urn:queryByParameter>
         </urn:controlActProcess>
      </urn:PRPA_IN201305UV02>
   </soap:Body>
</soap:Envelope>"""


@patch("app.soap.soap.lookup_patient")
def test_iti55_success(mock_lookup):
    mock_lookup.return_value = {
        "id": "9449305452",
        "name": [{"given": ["John"], "family": "Doe"}],
        "gender": "male",
        "birthDate": "1990-01-01",
        "address": [{"line": ["123 Fake St"], "city": "London", "postalCode": "W1 1AA"}],
        "telecom": [{"system": "phone", "value": "07700900000"}],
        "managingOrganization": {"identifier": {"value": "Y12345"}}
    }
    
    response = client.post("/iti55", content=MOCK_ITI55_REQUEST, headers={"Content-Type": "application/soap+xml"})
    assert response.status_code == 200
    assert b"PRPA_IN201306UV02" in response.content

def test_iti55_invalid_xml():
    response = client.post("/iti55", content="<invalid>", headers={"Content-Type": "application/soap+xml"})
    assert response.status_code == 200 # Returns SOAP Fault
    assert b"faultcode" in response.content or b"Sender" in response.content

@patch("app.soap.soap.lookup_patient")
def test_iti55_patient_not_found(mock_lookup):
    mock_lookup.return_value = None
    response = client.post("/iti55", content=MOCK_ITI55_REQUEST, headers={"Content-Type": "application/soap+xml"})
    assert response.status_code == 200
    assert b"PRPA_IN201306UV02" in response.content
    assert b"NF" in response.content # NullFlavor for patient not found

