import os
os.environ["REQUIRE_MTLS"] = "false"
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

def test_soap_exception_handler():
    with patch("app.soap.soap.iti39", side_effect=Exception("Test SOAP Exception")):
        # We simulate a hit to a SOAP route to trigger the exception
        response = client.post(
            "/SOAP/iti39", 
            content="<xml></xml>", 
            headers={"Content-Type": "application/soap+xml"}
        )
        assert response.status_code == 500
        assert "env:Receiver" in response.text
        assert "Internal Server Error" in response.text
        assert "application/soap+xml" in response.headers.get("content-type", "")

def test_fhir_exception_handler():
    @app.get("/FHIR/test_error")
    async def fhir_error():
        raise Exception("Test FHIR Exception")

    response = client.get("/FHIR/test_error")
    assert response.status_code == 500
    data = response.json()
    assert "issue" in data
    assert data["issue"][0]["severity"] == "fatal"
    assert data["issue"][0]["code"] == "exception"
    assert "TraceID" in data["issue"][0]["diagnostics"]

def test_generic_exception_handler():
    @app.get("/generic/test_error")
    async def generic_error():
        raise Exception("Test Generic Exception")

    response = client.get("/generic/test_error")
    assert response.status_code == 500
    data = response.json()
    assert data["title"] == "Internal Server Error"
    assert data["status"] == 500
    assert "TraceID" in data["detail"]
