from typing import Any, Dict

from app.audit.models import SAMLAttributes
from app.ccda.models.datatypes import CD  # adjust path


def process_saml_attributes(saml_header: dict) -> SAMLAttributes:
    """
    Process SAML attributes from SOAP header into a validated SAMLAttributes model.
    Role and PurposeOfUse are parsed as CD concept descriptors.
    """

    attr_map = {
        "urn:oasis:names:tc:xspa:1.0:subject:subject-id": "subject_id",
        "urn:oasis:names:tc:xspa:1.0:subject:organization": "organization",
        "urn:oasis:names:tc:xspa:1.0:subject:organization-id": "organization_id",
        "urn:nhin:names:saml:homeCommunityId": "home_community_id",
        "urn:oasis:names:tc:xacml:2.0:subject:role": "role",
        "urn:oasis:names:tc:xspa:1.0:subject:purposeofuse": "purpose_of_use",
        "urn:oasis:names:tc:xacml:2.0:resource:resource-id": "resource_id",
    }

    raw: Dict[str, Any] = {}

    attributes = saml_header.get("Attribute", [])
    if not isinstance(attributes, list):
        raise ValueError("Invalid SAML header: Attribute must be a list")

    for attribute in attributes:
        urn = attribute.get("@Name")
        if urn not in attr_map:
            continue

        key = attr_map[urn]
        value = attribute.get("AttributeValue")

        # Role and PurposeOfUse come wrapped, e.g. {"Role": {...}} / {"PurposeForUse": {...}}
        if key == "role" and isinstance(value, dict):
            cd_payload = value.get("Role") or value
            raw["role"] = CD.model_validate(cd_payload)

        elif key == "purpose_of_use" and isinstance(value, dict):
            cd_payload = value.get("PurposeForUse") or value
            raw["purpose_of_use"] = CD.model_validate(cd_payload)

        else:
            raw[key] = value

    return SAMLAttributes.model_validate(raw)