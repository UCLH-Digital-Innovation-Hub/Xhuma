import uuid

import xmltodict

from .constants import COMMUNITY_ID, REGISTRY_ID
from .helpers import create_envelope, create_header


async def iti_39_response(message_id: str, document_id: str, document):

    # base64 encode the document
    # base64_bytes = base64.b64encode(document.encode("utf-8")).decode("utf-8")
    # print(type(base64_bytes))
    body = {
        "ns4:RetrieveDocumentSetResponse": {
            "@xmlns:ns4": "urn:ihe:iti:xds-b:2007",
            "@xmlns:ns8": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
            "ns8:RegistryResponse": {
                "@id": uuid.uuid4(),
                "@status": "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success",
                # "@xmlns": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
            },
            "ns4:DocumentResponse": {
                "ns4:HomeCommunityId": {"#text": f"urn:oid:{COMMUNITY_ID}"},
                "ns4:RepositoryUniqueId": {"#text": REGISTRY_ID},
                "ns4:DocumentUniqueId": {"#text": document_id},
                "ns4:mimeType": {"#text": "text/xml"},
                "ns4:Document": document,
            },
        },
    }

    soap_response = create_envelope(create_header("urn:ihe:iti:2007:CrossGatewayRetrieveResponse", message_id), body)

    # print(f"ITI39 response: {soap_response}")

    # soap_response = create_envelope(
    #     create_header("urn:ihe:iti:2007:RetrieveDocumentSetResponse", "test"), body
    # )

    # Verify that all values are serializable
    # TODO DELETE THIS?
    def ensure_serializable(data):
        if isinstance(data, bytes):
            return data.decode("utf-8")  # Decode bytes to string
        elif isinstance(data, dict):
            return {k: ensure_serializable(v) for k, v in data.items()}  # Recurse
        elif isinstance(data, list):
            return [ensure_serializable(item) for item in data]  # Recurse for lists
        else:
            return data  # Return as-is for strings, numbers, etc.

    soap_response = ensure_serializable(soap_response)

    # pprint.pprint(soap_response)
    # print(type(soap_response))

    # with open(f"{document_id}.xml", "w") as output:
    #     output.write(xmltodict.unparse(soap_response, pretty=True))

    return xmltodict.unparse(soap_response, pretty=True)
