from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.mtls import MTLSMiddleware

app = FastAPI()
app.add_middleware(MTLSMiddleware)


@app.get("/")
def root():
    return {"message": "Hello World"}


@app.get("/secure")
def secure():
    return {"message": "Secure Data"}


client = TestClient(app)


def test_mtls_disabled_by_default(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "false")
    response = client.get("/secure")
    assert response.status_code == 200


def test_mtls_enabled_no_header(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    response = client.get("/secure")
    assert response.status_code == 403
    assert response.json() == {"detail": "Client Certificate Required"}


def test_mtls_enabled_with_header(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    monkeypatch.setattr("app.middleware.mtls._verify_epic_cert", lambda x: True)
    response = client.get("/secure", headers={"X-ARR-ClientCert": "MIID..."})
    assert response.status_code == 200


def test_mtls_public_paths(monkeypatch):
    monkeypatch.setenv("REQUIRE_MTLS", "true")
    # / is in public_paths
    response = client.get("/")
    assert response.status_code == 200


DUMMY_B64_DER = "MIICqjCCAZKgAwIBAgIUXAFlQMhjDFeL0iD6IbgKSGuCbBEwDQYJKoZIhvcNAQELBQAwDzENMAsGA1UEAwwEdGVzdDAeFw0yNjA4MTcxMzMxNTNaFw0yNjA4MjcxMzMxNTNaMA8xDTALBgNVBAMMBHRlc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCMF6BKbKz1mgZqx2IiDylZCMMw2Cbs/dTMEqIT4eXxEQ2mAjvIHgBA+p7SLEpinGCMVNWWNiIzpd5e1Nl4p0y3wFC1ns/fOIOs7v8WLYLsjU0Z8JlT7UeOq6ASe3czHcM+w8lK36l+54b0RC9g/3cgu9Dtslm2to3yf/nhmYPlMMZNfnzD5EBK9c5TeZ0g5/4pQfqE8Yf5C+RQajwMheH8HvtKq9Wyb1d0eclBiCk5Fu9lng1WejSn6O0IyE14TACuDm6KfEzGMAHindqQZqH8cxIEUL1FwzdgsvcFqvmSicqlMbYsGYk+SmjNytT1e55WiXGD34fJh/3fZKfI7KQDAgMBAAEwDQYJKoZIhvcNAQELBQADggEBAEStn8PYUNTn6cFNqUO22k3lBHVv3robgxgHGf9bzf5r7Q1mBDay5CMLUA/AR9FRjn8H2Qxgfnqb4lSfGToHzfPE76n6C2uZF0ctQILRneNIq3k4d4WbBAY+VFjWEzyOoiKM11kDrmGGmrx9Un7KvCcVAk+7Y9yNglmCsKnhMQiCdn03gvgEcyu+KYVNunu1KffcKPtmpZkOH2WcGfggCyc5nbOFySkOCxAjHa6czlMi7QvaeNOUod5PjW9rzYv3yROzEXADdJRF5dUGvj1SQXQfPn2WfNcRICvh59CUpKkUfbI3GRkQJ9Tsj24vXtdauGeKMQxYakwFd/PfjFqbktA="

DUMMY_PEM = """-----BEGIN CERTIFICATE-----
MIICqjCCAZKgAwIBAgIUXAFlQMhjDFeL0iD6IbgKSGuCbBEwDQYJKoZIhvcNAQEL
BQAwDzENMAsGA1UEAwwEdGVzdDAeFw0yNjA4MTcxMzMxNTNaFw0yNjA4MjcxMzMx
NTNaMA8xDTALBgNVBAMMBHRlc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK
AoIBAQCMF6BKbKz1mgZqx2IiDylZCMMw2Cbs/dTMEqIT4eXxEQ2mAjvIHgBA+p7S
LEpinGCMVNWWNiIzpd5e1Nl4p0y3wFC1ns/fOIOs7v8WLYLsjU0Z8JlT7UeOq6AS
e3czHcM+w8lK36l+54b0RC9g/3cgu9Dtslm2to3yf/nhmYPlMMZNfnzD5EBK9c5T
eZ0g5/4pQfqE8Yf5C+RQajwMheH8HvtKq9Wyb1d0eclBiCk5Fu9lng1WejSn6O0I
yE14TACuDm6KfEzGMAHindqQZqH8cxIEUL1FwzdgsvcFqvmSicqlMbYsGYk+SmjN
ytT1e55WiXGD34fJh/3fZKfI7KQDAgMBAAEwDQYJKoZIhvcNAQELBQADggEBAESt
n8PYUNTn6cFNqUO22k3lBHVv3robgxgHGf9bzf5r7Q1mBDay5CMLUA/AR9FRjn8H
2Qxgfnqb4lSfGToHzfPE76n6C2uZF0ctQILRneNIq3k4d4WbBAY+VFjWEzyOoiKM
11kDrmGGmrx9Un7KvCcVAk+7Y9yNglmCsKnhMQiCdn03gvgEcyu+KYVNunu1Kffc
KPtmpZkOH2WcGfggCyc5nbOFySkOCxAjHa6czlMi7QvaeNOUod5PjW9rzYv3yROz
EXADdJRF5dUGvj1SQXQfPn2WfNcRICvh59CUpKkUfbI3GRkQJ9Tsj24vXtdauGeK
MQxYakwFd/PfjFqbktA=
-----END CERTIFICATE-----"""

DUMMY_THUMBPRINT = "0c7492ba076f590d718141434b43b4a15db5b9a6b2f6a6b6c32790f451a82f50"


def test_verify_epic_cert_valid(monkeypatch):
    monkeypatch.setenv("EPIC_CA_CERT", DUMMY_PEM)  # Serves as its own CA
    monkeypatch.setenv("MTLS_TRUSTED_THUMBPRINTS", f"abc1234,{DUMMY_THUMBPRINT},xyz789")
    from app.middleware.mtls import _verify_epic_cert

    assert _verify_epic_cert(DUMMY_B64_DER) is True


def test_verify_epic_cert_invalid_thumbprint(monkeypatch):
    monkeypatch.setenv("EPIC_CA_CERT", DUMMY_PEM)
    monkeypatch.setenv(
        "MTLS_TRUSTED_THUMBPRINTS", "wrongthumbprint,anotherwrongthumbprint"
    )
    from app.middleware.mtls import _verify_epic_cert

    assert _verify_epic_cert(DUMMY_B64_DER) is False


def test_verify_epic_cert_malformed(monkeypatch):
    monkeypatch.setenv("EPIC_CA_CERT", DUMMY_PEM)
    monkeypatch.setenv("MTLS_TRUSTED_THUMBPRINTS", DUMMY_THUMBPRINT)
    from app.middleware.mtls import _verify_epic_cert

    assert _verify_epic_cert("not-a-valid-b64-der") is False
