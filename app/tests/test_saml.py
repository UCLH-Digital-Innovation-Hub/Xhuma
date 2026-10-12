import pytest

from app.soap.saml_helper import InvalidSAMLContext, extract_trusted_saml_assertion, validate_saml_attributes


def test_missing_assertion():
    with pytest.raises(InvalidSAMLContext, match="Missing SAML assertion"):
        extract_trusted_saml_assertion({"Header": {"Security": {}}})


def test_multiple_assertions():
    with pytest.raises(InvalidSAMLContext, match="Expected exactly one SAML assertion"):
        extract_trusted_saml_assertion({"Header": {"Security": {"Assertion": [{}, {}]}}})


def test_missing_issuer():
    with pytest.raises(InvalidSAMLContext, match="Missing SAML issuer"):
        extract_trusted_saml_assertion({"Header": {"Security": {"Assertion": {"SomeField": "Value"}}}})


def test_untrusted_issuer(monkeypatch):
    monkeypatch.setenv("SAML_TRUSTED_ISSUER", "urn:nhs:trusted")
    with pytest.raises(InvalidSAMLContext, match="Untrusted SAML issuer"):
        extract_trusted_saml_assertion({"Header": {"Security": {"Assertion": {"Issuer": "urn:epic:untrusted"}}}})


def test_trusted_issuer(monkeypatch):
    monkeypatch.setenv("SAML_TRUSTED_ISSUER", "urn:nhs:trusted")
    assertion = extract_trusted_saml_assertion({"Header": {"Security": {"Assertion": {"Issuer": "urn:nhs:trusted"}}}})
    assert assertion["Issuer"] == "urn:nhs:trusted"


def test_validate_saml_attributes_missing_fields():
    class MockSAMLAttrs:
        subject_id = "test_sub"
        organization = None
        organization_id = "org_id"
        role = None

    attrs = MockSAMLAttrs()
    with pytest.raises(InvalidSAMLContext, match="Incomplete SAML security context"):
        validate_saml_attributes(attrs)


def test_validate_saml_attributes_valid():
    class MockSAMLAttrs:
        subject_id = "test_sub"
        organization = "test_org"
        organization_id = "org_id"
        role = "doctor"

    attrs = MockSAMLAttrs()
    validate_saml_attributes(attrs)  # Should not raise
