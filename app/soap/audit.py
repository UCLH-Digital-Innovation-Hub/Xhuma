def process_saml_attributes(saml_header: dict) -> dict:
    """
    Function to process saml atributes from SOAP header

    Args:
    - saml_header (dict): The SAML attributes from the SOAP header as dictionary

    Returns:
    - mapped dictionary of SAML audit headers


    """
    saml_attributes = {}
    attr_map = {
        "urn:oasis:names:tc:xspa:1.0:subject:subject-id": "subject_id",
        "urn:oasis:names:tc:xspa:1.0:subject:organization": "organization",
        "urn:oasis:names:tc:xspa:1.0:subject:organization-id": "organization_id",
        "urn:nhin:names:saml:homeCommunityId": "home_community_id",
        "urn:oasis:names:tc:xacml:2.0:subject:role": "role",
        "urn:oasis:names:tc:xspa:1.0:subject:purposeofuse": "purpose_of_use",
        "urn:oasis:names:tc:xacml:2.0:resource:resource-id": "resource_id",
    }
    for attribute in saml_header["Attribute"]:
        # Set the attribute value to the corresponding attribute in the SAMLAttributes dataclass
        # setattr(saml_attributes, attr_map[attribute['@Name']], attribute['AttributeValue'])
        saml_attributes[attr_map[attribute["@Name"]]] = attribute["AttributeValue"]

    # print(saml_attributes)
    return saml_attributes
