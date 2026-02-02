import os
import xmltodict

from app.audit.audit import process_saml_attributes
from app.security import create_jwt


xml39 = '<AttributeStatement><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:subject-id"><AttributeValue>CONE, Stephen</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization"><AttributeValue>UCLH - University College London Hospitals - TST</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization-id"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:nhin:names:saml:homeCommunityId"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><AttributeValue><Role xsi:type="CE" code="224608005" codeSystem="2.16.840.1.113883.6.96" codeSystemName="SNOMED_CT" displayName="Administrative healthcare staff" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"><AttributeValue><PurposeForUse xsi:type="CE" code="TREATMENT" codeSystem="2.16.840.1.113883.3.18.7.1" codeSystemName="nhin-purpose" displayName="Treatment" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:resource:resource-id"><AttributeValue>9690937278^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</AttributeValue></Attribute></AttributeStatement>'
xml38 = '<AttributeStatement><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:subject-id"><AttributeValue>CONE, Stephen</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization"><AttributeValue>UCLH - University College London Hospitals - TST</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization-id"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:nhin:names:saml:homeCommunityId"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><AttributeValue><Role xsi:type="CE" code="224608005" codeSystem="2.16.840.1.113883.6.96" codeSystemName="SNOMED_CT" displayName="Administrative healthcare staff" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"><AttributeValue><PurposeForUse xsi:type="CE" code="TREATMENT" codeSystem="2.16.840.1.113883.3.18.7.1" codeSystemName="nhin-purpose" displayName="Treatment" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:resource:resource-id"><AttributeValue>9690937278^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</AttributeValue></Attribute></AttributeStatement>'


def _parse(xml: str) -> dict:
    # xmltodict returns dict with AttributeStatement at top level
    return xmltodict.parse(xml)


def _assert_common_fields(saml):
    # saml is a Pydantic model now (SAMLAttributes)
    assert saml.subject_id == "CONE, Stephen"
    assert saml.organization == "UCLH - University College London Hospitals - TST"
    assert saml.organization_id == "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100"
    assert saml.home_community_id == "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100"

    # Role is CD
    assert saml.role is not None
    assert saml.role.codeSystem == "2.16.840.1.113883.6.96"
    assert saml.role.codeSystemName == "SNOMED_CT"
    assert saml.role.code == "224608005"
    assert saml.role.displayName == "Administrative healthcare staff"

    # Purpose of Use is CD
    assert saml.purpose_of_use is not None
    assert saml.purpose_of_use.code == "TREATMENT"
    assert saml.purpose_of_use.codeSystem == "2.16.840.1.113883.3.18.7.1"
    assert saml.purpose_of_use.codeSystemName == "nhin-purpose"
    assert saml.purpose_of_use.displayName == "Treatment"

    # Resource id should be unescaped (&amp; -> &)
    assert saml.resource_id == "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO"

    # Computed extraction of NHS number
    # assert saml.subject_nhs_number == "9690937278"


def test_saml_iti39():
    saml_header = _parse(xml39)
    saml = process_saml_attributes(saml_header["AttributeStatement"])
    _assert_common_fields(saml)


def test_saml_iti38():
    saml_header = _parse(xml38)
    saml = process_saml_attributes(saml_header["AttributeStatement"])
    _assert_common_fields(saml)

def test_create_jwt():
    saml_header = _parse(xml39)
    saml = process_saml_attributes(saml_header["AttributeStatement"])

    token = create_jwt(saml)

    assert token is not None
    assert isinstance(token, str)


# def test_subject_ref_computed_field(monkeypatch):
#     """
#     Computed subject_ref depends on AUDIT_SUBJECT_SECRET.
#     """
#     monkeypatch.setenv("AUDIT_SUBJECT_SECRET", "x" * 40)

#     saml_header = _parse(xml38)
#     saml = process_saml_attributes(saml_header["AttributeStatement"])

#     # subject_ref should now be computed
#     assert saml.subject_ref is not None
#     assert saml.subject_ref.startswith("nhsref:v1:")
