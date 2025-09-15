import xmltodict

from app.soap.audit import process_saml_attributes

xml39 = '<AttributeStatement><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:subject-id"><AttributeValue>CONE, Stephen</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization"><AttributeValue>UCLH - University College London Hospitals - TST</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization-id"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:nhin:names:saml:homeCommunityId"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><AttributeValue><Role xsi:type="CE" code="224608005" codeSystem="2.16.840.1.113883.6.96" codeSystemName="SNOMED_CT" displayName="Administrative healthcare staff" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"><AttributeValue><PurposeForUse xsi:type="CE" code="TREATMENT" codeSystem="2.16.840.1.113883.3.18.7.1" codeSystemName="nhin-purpose" displayName="Treatment" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:resource:resource-id"><AttributeValue>9690937278^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</AttributeValue></Attribute></AttributeStatement>'
xml38 = '<AttributeStatement><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:subject-id"><AttributeValue>CONE, Stephen</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization"><AttributeValue>UCLH - University College London Hospitals - TST</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization-id"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:nhin:names:saml:homeCommunityId"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><AttributeValue><Role xsi:type="CE" code="224608005" codeSystem="2.16.840.1.113883.6.96" codeSystemName="SNOMED_CT" displayName="Administrative healthcare staff" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"><AttributeValue><PurposeForUse xsi:type="CE" code="TREATMENT" codeSystem="2.16.840.1.113883.3.18.7.1" codeSystemName="nhin-purpose" displayName="Treatment" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:resource:resource-id"><AttributeValue>9690937278^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</AttributeValue></Attribute></AttributeStatement>'


def test_saml_iti39():
    saml_header = xmltodict.parse(xml39)
    # pprint.pprint(saml_header)
    for atribute in saml_header["AttributeStatement"]["Attribute"]:
        print(atribute["@Name"])
        print(atribute["AttributeValue"])
    saml_attributes = process_saml_attributes(saml_header)
    assert saml_attributes["subject_id"] == "CONE, Stephen"
    assert (
        saml_attributes["organization"]
        == "UCLH - University College London Hospitals - TST"
    )
    assert (
        saml_attributes["organization_id"]
        == "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100"
    )
    assert (
        saml_attributes["home_community_id"]
        == "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100"
    )
    assert saml_attributes["role"]["Role"]["@codeSystem"] == "2.16.840.1.113883.6.96"
    assert saml_attributes["purpose_of_use"] == {
        "PurposeForUse": {
            "@xsi:type": "CE",
            "@code": "TREATMENT",
            "@codeSystem": "2.16.840.1.113883.3.18.7.1",
            "@codeSystemName": "nhin-purpose",
            "@displayName": "Treatment",
            "@xmlns": "urn:hl7-org:v3",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        }
    }
    assert (
        saml_attributes["resource_id"] == "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO"
    )


def test_saml_iti38():
    saml_header = xmltodict.parse(xml38)
    saml_attributes = process_saml_attributes(saml_header)
    assert saml_attributes["subject_id"] == "CONE, Stephen"
    assert (
        saml_attributes["organization"]
        == "UCLH - University College London Hospitals - TST"
    )
    assert (
        saml_attributes["organization_id"]
        == "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100"
    )
    assert (
        saml_attributes["home_community_id"]
        == "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100"
    )
    assert saml_attributes["role"]["Role"]["@codeSystem"] == "2.16.840.1.113883.6.96"
    assert saml_attributes["purpose_of_use"] == {
        "PurposeForUse": {
            "@xsi:type": "CE",
            "@code": "TREATMENT",
            "@codeSystem": "2.16.840.1.113883.3.18.7.1",
            "@codeSystemName": "nhin-purpose",
            "@displayName": "Treatment",
            "@xmlns": "urn:hl7-org:v3",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        }
    }
    assert (
        saml_attributes["resource_id"] == "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO"
    )
