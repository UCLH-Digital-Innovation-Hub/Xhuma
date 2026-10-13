import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree.ElementTree import ParseError

import httpx
import pytest

from app.nir.sandbox import TEST_PATIENTS, check_sandbox, main
from app.soap.nir_models import NIRDocumentReference, NIRQueryResponse, NIRRetrievedDocument, NIRRetrieveResponse

SUCCESS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"
FAILURE = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"


def document(reference, **kwargs):
    return NIRDocumentReference(
        home_community_id=reference[0], repository_unique_id=reference[1], document_unique_id=reference[2], **kwargs
    )


@pytest.fixture
def sandbox_client():
    client = SimpleNamespace(config=SimpleNamespace(environment="sandbox"))
    client.query_documents = AsyncMock(
        side_effect=[
            NIRQueryResponse(status=SUCCESS, documents=[document(reference) for reference in references])
            for references in TEST_PATIENTS.values()
        ]
    )
    client.retrieve_documents = AsyncMock(
        side_effect=[
            NIRRetrieveResponse(
                status=SUCCESS,
                documents=[
                    NIRRetrievedDocument(**document(reference).model_dump(), content=b"synthetic document")
                    for reference in references
                ],
            )
            for references in TEST_PATIENTS.values()
        ]
    )
    return client


@pytest.mark.asyncio
async def test_all_published_patients_and_retrieval(sandbox_client):
    results = await check_sandbox(sandbox_client, retrieve=True)
    assert all(result["success"] for result in results)
    assert [result["nhs_number"] for result in results] == list(TEST_PATIENTS)
    assert [result["document_count"] for result in results] == [1, 2, 2]
    assert sandbox_client.retrieve_documents.await_count == 3
    assert results[0]["retrieval"]["documents"][0]["byte_length"] == 18
    assert "synthetic document" not in str(results)


@pytest.mark.asyncio
async def test_missing_community_reference_is_failure(sandbox_client):
    # A matching UID from another community cannot satisfy the published reference.
    responses = list(sandbox_client.query_documents.side_effect)
    expected = TEST_PATIENTS["9932549118"]
    wrong_community = ("urn:oid:1.2.34.567.8.1", expected[1][1], expected[1][2])
    responses[2] = NIRQueryResponse(status=SUCCESS, documents=[document(expected[0]), document(wrong_community)])
    sandbox_client.query_documents.side_effect = responses
    results = await check_sandbox(sandbox_client)
    assert not results[2]["success"]
    assert results[2]["missing_documents"][0]["home_community_id"] == "urn:oid:1.2.34.567.8.2"


@pytest.mark.asyncio
async def test_additional_documents_are_reported_and_retrieved(sandbox_client):
    responses = list(sandbox_client.query_documents.side_effect)
    additional = document(("urn:oid:1.2.34.567.8.1", "1.1.4567332.1.7", "extra-document"))
    responses[2].documents.append(additional)
    sandbox_client.query_documents.side_effect = responses
    retrievals = list(sandbox_client.retrieve_documents.side_effect)
    retrievals[2].documents.append(NIRRetrievedDocument(**additional.model_dump(), content=b"extra"))
    sandbox_client.retrieve_documents.side_effect = retrievals
    results = await check_sandbox(sandbox_client, retrieve=True)
    assert results[2]["success"]
    assert results[2]["document_count"] == 3
    assert results[2]["additional_documents"][0]["document_unique_id"] == "extra-document"
    assert len(sandbox_client.retrieve_documents.await_args_list[2].args[0]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status,errors", [(FAILURE, []), (SUCCESS, ["Gateway failed"])])
async def test_unsuccessful_soap_response_fails(sandbox_client, status, errors):
    responses = list(sandbox_client.query_documents.side_effect)
    responses[0].status = status
    responses[0].errors = errors
    sandbox_client.query_documents.side_effect = responses
    results = await check_sandbox(sandbox_client, retrieve=True)
    assert not results[0]["success"]
    assert "retrieval" not in results[0]
    assert sandbox_client.retrieve_documents.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "missing", "soap"])
async def test_retrieval_must_return_all_nonempty_payloads(sandbox_client, failure):
    responses = list(sandbox_client.retrieve_documents.side_effect)
    if failure == "empty":
        responses[0].documents[0].content = b""
    elif failure == "missing":
        responses[0].documents = []
    else:
        responses[0].status = FAILURE
    sandbox_client.retrieve_documents.side_effect = responses
    results = await check_sandbox(sandbox_client, retrieve=True)
    assert not results[0]["success"]
    assert not results[0]["retrieval"]["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("sandbox unavailable"), ParseError("invalid SOAP XML")])
async def test_response_error_reports_failure_and_checks_remaining_patients(sandbox_client, error):
    responses = list(sandbox_client.query_documents.side_effect)
    responses[0] = error
    sandbox_client.query_documents.side_effect = responses
    results = await check_sandbox(sandbox_client)
    assert not results[0]["success"]
    assert type(error).__name__ in results[0]["errors"][0]
    assert all(result["success"] for result in results[1:])


@pytest.mark.asyncio
async def test_non_sandbox_checks_are_rejected_before_requests(sandbox_client):
    sandbox_client.config.environment = "int"
    with pytest.raises(ValueError, match="NIR_ENV=sandbox"):
        await check_sandbox(sandbox_client)
    sandbox_client.query_documents.assert_not_awaited()


def test_cli_returns_failure_for_invalid_environment(monkeypatch, capsys):
    monkeypatch.setenv("NIR_ENV", "unknown")
    assert main([]) == 1
    assert '"success": false' in capsys.readouterr().out


@pytest.mark.parametrize("failure", [None, "missing", "soap", "network"])
def test_cli_exit_status_reflects_patient_checks(sandbox_client, monkeypatch, capsys, failure):
    monkeypatch.setenv("NIR_ENV", "sandbox")
    monkeypatch.delenv("NIR_BASE_URL", raising=False)
    responses = list(sandbox_client.query_documents.side_effect)
    if failure == "missing":
        responses[0].documents = []
    elif failure == "soap":
        responses[0].status = FAILURE
    elif failure == "network":
        responses[0] = httpx.ConnectError("sandbox unavailable")
    sandbox_client.query_documents.side_effect = responses
    context_manager = AsyncMock()
    context_manager.__aenter__.return_value = sandbox_client
    monkeypatch.setattr("app.nir.sandbox.NIRClient", lambda config: context_manager)

    assert main([]) == (1 if failure else 0)
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(results) == 3
    assert results[0]["success"] == (failure is None)
