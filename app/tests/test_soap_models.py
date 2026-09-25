import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import xmltodict

from app.soap import responses
from app.soap.models import ITI38Request, ITI39Request, ITI55Request
from app.soap.responses import iti_38
from app.soap.responses.iti_55 import _select_usual_name

PDS_RESULTS = Path(__file__).parent / "fixtures" / "pdsresults"


def test_iti55_request_extracts_nhs_number_from_repeating_values():
    request = ITI55Request.model_validate(
        {
            "Header": {"MessageID": "message-55"},
            "Body": {
                "PRPA_IN201305UV02": {
                    "controlActProcess": {
                        "queryByParameter": {
                            "queryId": {"@root": "query-55"},
                            "parameterList": {
                                "livingSubjectId": {
                                    "value": [
                                        {"@root": "other", "@extension": "local-id"},
                                        {
                                            "@root": "2.16.840.1.113883.2.1.4.1",
                                            "@extension": "9999999999",
                                        },
                                    ]
                                }
                            },
                        }
                    }
                }
            },
        }
    )

    assert request.header.message_id == "message-55"
    assert request.patient_identifier("2.16.840.1.113883.2.1.4.1") == "9999999999"


def test_iti38_request_supports_adhoc_query_and_single_slot():
    request = ITI38Request.model_validate(
        {
            "Header": {"MessageID": "message-38"},
            "Body": {
                "AdhocQueryRequest": {
                    "AdhocQuery": {
                        "@id": "query-38",
                        "Slot": {
                            "@name": "$XDSDocumentEntryPatientId",
                            "ValueList": {"Value": "'9999999999^^^&oid&ISO'"},
                        },
                    }
                }
            },
        }
    )

    assert request.body.query.query_id == "query-38"
    assert request.body.query.slot_value("$XDSDocumentEntryPatientId") == "'9999999999^^^&oid&ISO'"


def test_iti38_request_supports_cross_gateway_query_and_repeating_values():
    """Cover the alternate schema wrapper and its repeating Value cardinality."""

    request = ITI38Request.model_validate(
        {
            "Header": {"MessageID": "synthetic-message-38"},
            "Body": {
                "CrossGatewayQuery": {
                    "AdhocQuery": {
                        "@id": "synthetic-query-38",
                        "Slot": {
                            "@name": "$XDSDocumentEntryPatientId",
                            "ValueList": {
                                "Value": [
                                    "'9999999999^^^&2.16.840.1.113883.2.1.4.1&ISO'",
                                    "'9999999999^^^&synthetic-secondary-authority&ISO'",
                                ]
                            },
                        },
                    }
                }
            },
        }
    )

    assert request.body.query.query_id == "synthetic-query-38"
    assert request.body.query.slot_value("$XDSDocumentEntryPatientId") == (
        "'9999999999^^^&2.16.840.1.113883.2.1.4.1&ISO'"
    )


def test_iti39_request_exposes_document_and_reply_to():
    request = ITI39Request.model_validate(
        {
            "Header": {
                "MessageID": "message-39",
                "ReplyTo": {"Address": "https://example.nhs.uk/callback"},
            },
            "Body": {
                "RetrieveDocumentSetRequest": {
                    "DocumentRequest": {
                        "HomeCommunityId": "urn:oid:1.2.3",
                        "RepositoryUniqueId": "repository-id",
                        "DocumentUniqueId": "document-id",
                    }
                }
            },
        }
    )

    document = request.body.retrieve_document_set_request.first_document
    assert document.document_unique_id == "document-id"
    assert request.header.reply_to.address == "https://example.nhs.uk/callback"


def test_iti39_request_selects_first_document_from_repeating_requests():
    """The endpoint intentionally handles one document from an allowed list."""

    request = ITI39Request.model_validate(
        {
            "Header": {"MessageID": "synthetic-message-39"},
            "Body": {
                "RetrieveDocumentSetRequest": {
                    "DocumentRequest": [
                        {"DocumentUniqueId": "synthetic-document-1"},
                        {"DocumentUniqueId": "synthetic-document-2"},
                    ]
                }
            },
        }
    )

    document = request.body.retrieve_document_set_request.first_document
    assert document.document_unique_id == "synthetic-document-1"


def test_iti55_name_selection_falls_back_when_use_is_missing():
    name = {"given": ["Ada"], "family": "Lovelace"}

    assert _select_usual_name({"name": [name]}) == name


@pytest.mark.asyncio
async def test_iti55_response_uses_usual_name_and_preserves_xml_shape():
    patient = {
        "id": "9999999999",
        "gender": "female",
        "birthDate": "1980-01-02",
        "name": [
            {"use": "old", "given": ["Augusta"], "family": "King"},
            {"use": "usual", "given": ["Ada"], "family": "Lovelace"},
        ],
        "generalPractitioner": [{"identifier": {"value": "GP01"}}],
    }
    query = {
        "queryId": {"@root": "query-55"},
        "parameterList": {
            "livingSubjectId": {
                "value": {
                    "@root": "2.16.840.1.113883.2.1.4.1",
                    "@extension": "9999999999",
                }
            }
        },
    }

    xml = await responses.iti_55_response("message-55", patient, query)
    envelope = xmltodict.parse(xml)["s:Envelope"]
    response = envelope["s:Body"]["PRPA_IN201306UV02"]

    assert envelope["s:Header"]["a:RelatesTo"] == "message-55"
    assert response["interactionId"]["@extension"] == "PRPA_IN201306UV02"
    assert response["controlActProcess"]["queryByParameter"] == query
    name = response["controlActProcess"]["subject"]["registrationEvent"]["subject1"]["patient"]["patientPerson"]["name"]
    assert name == {"given": "Ada", "family": "Lovelace"}


@pytest.mark.parametrize(
    ("fixture_name", "given", "family", "gp_code", "birth_time"),
    [
        ("9690937278.json", "Lucien", "SAMUAL", "E82665", "19381211"),
        ("9690937286.json", "HORACE", "SKELLY", "B82617", "19250421"),
    ],
)
@pytest.mark.asyncio
async def test_iti55_response_with_pds_fixture(fixture_name, given, family, gp_code, birth_time):
    """Exercise ITI-55 mapping with representative PDS response payloads."""

    with (PDS_RESULTS / fixture_name).open(encoding="utf-8") as fixture:
        patient = json.load(fixture)

    xml = await responses.iti_55_response("message-55", patient, {"queryId": {"@root": "query-55"}})
    response = xmltodict.parse(xml)["s:Envelope"]["s:Body"]["PRPA_IN201306UV02"]
    patient_xml = response["controlActProcess"]["subject"]["registrationEvent"]["subject1"]["patient"]
    person_xml = patient_xml["patientPerson"]

    assert patient_xml["id"][0]["@extension"] == patient["id"]
    assert person_xml["name"] == {"given": given, "family": family}
    assert person_xml["administrativeGenderCode"]["@code"] == "M"
    assert person_xml["birthTime"]["@value"] == birth_time
    assert patient_xml["providerOrganization"]["id"]["id"] == gp_code


@pytest.mark.asyncio
async def test_iti38_response_preserves_registry_metadata_shape(monkeypatch):
    monkeypatch.setattr(iti_38.redis_client, "get", lambda _: b"document-id")
    monkeypatch.setattr(iti_38, "attempt_audit", AsyncMock())

    xml = await responses.iti_38_response(
        request=None,
        nhsno="9999999999",
        ceid="NOCEID",
        queryid="query-38",
        saml_attrs=None,
    )
    response = xmltodict.parse(xml)["s:Envelope"]["s:Body"]["AdhocQueryResponse"]
    document = response["RegistryObjectList"]["ExtrinsicObject"]

    assert response["@status"].endswith(":Success")
    assert document["@id"] == "document-id"
    assert [slot["@name"] for slot in document["Slot"]] == [
        "sourcePatientId",
        "sourcePatientInfo",
        "languageCode",
        "size",
        "repositoryUniqueId",
    ]


@pytest.mark.asyncio
async def test_iti39_response_decodes_cached_document_bytes():
    xml = await responses.iti_39_response("message-39", "document-id", b"<ClinicalDocument/>")
    response = xmltodict.parse(xml)["s:Envelope"]["s:Body"]["ns4:RetrieveDocumentSetResponse"]

    assert response["ns8:RegistryResponse"]["@status"].endswith(":Success")
    assert response["ns4:DocumentResponse"]["ns4:Document"] == "<ClinicalDocument/>"


@pytest.mark.asyncio
async def test_iti39_error_preserves_registry_error_shape():
    xml = await responses.iti_39_error("message-39", "missing-document")
    response = xmltodict.parse(xml)["s:Envelope"]["s:Body"]["ns4:RetrieveDocumentSetResponse"]
    registry_response = response["rs:RegistryResponse"]
    error = registry_response["rs:RegistryErrorList"]["rs:RegistryError"]

    assert registry_response["@status"].endswith(":Failure")
    assert error["@errorCode"] == "XDSDocumentUniqueIdError"
    assert error["@codeContext"] == "Document with Id missing-document not found"
