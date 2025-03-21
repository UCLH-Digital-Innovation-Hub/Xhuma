import asyncio

import pytest

from app.gpconnect import gpconnect

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.asyncio
async def test_gpconnect():
    audit_dict = {
        "subject_id": "CONE, Stephen",
        "organization": "UCLH - University College London Hospitals - TST",
        "organization_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "home_community_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "role": {
            "Role": {
                "@codeSystem": "2.16.840.1.113883.6.96",
                "@code": "224608005",
                "@codeSystemName": "SNOMED_CT",
                "@displayName": "Administrative healthcare staff",
                "@xmlns": "urn:hl7-org:v3",
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            }
        },
        "purpose_of_use": {
            "PurposeForUse": {
                "@xsi:type": "CE",
                "@code": "TREATMENT",
                "@codeSystem": "2.16.840.1.113883.3.18.7.1",
                "@codeSystemName": "nhin-purpose",
                "@displayName": "Treatment",
                "@xmlns": "urn:hl7-org:v3",
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            },
        },
        "resource_id": "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO",
    }

    result = await gpconnect(9690937278, audit_dict)
    assert result["resourceType"] == "Patient"
