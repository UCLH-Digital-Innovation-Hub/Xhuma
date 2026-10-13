"""Check the NHS published synthetic patients: python -m app.nir.sandbox."""

import argparse
import asyncio
import json
from xml.etree.ElementTree import ParseError

import httpx

from app.soap.nir_models import NIRDocumentReference

from .client import NIRClient
from .config import NIRConfig

# https://digital.nhs.uk/developer/api-catalogue/national-imaging-registry-api/test-data
# These are the published minimum expectations, rather than a snapshot of every
# current sandbox document. The sandbox may add documents to an existing patient.
# A document UID can occur in multiple communities: retain all three identifiers.
TEST_PATIENTS = {
    "9694116422": (("urn:oid:1.2.34.567.8.1", "1.1.4567332.1.7", "1.2.42.20251212161454.2"),),
    "9898841141": (
        ("urn:oid:1.2.34.567.8.1", "1.1.4567332.1.7", "2.25.90214658647374166344513344800740950001"),
        ("urn:oid:1.2.34.567.8.1", "1.1.4567332.1.7", "1.2.42.20260115152435.2"),
    ),
    "9932549118": (
        ("urn:oid:1.2.34.567.8.1", "1.1.4567332.1.7", "1.2.42.20251212161454.4"),
        ("urn:oid:1.2.34.567.8.2", "1.1.4567332.1.2", "2.25.90214658647374166344513344800740950001"),
    ),
}


def _identifiers(document: NIRDocumentReference) -> tuple[str, str, str]:
    """Use the full retrieval address when comparing expected and actual documents."""
    return document.home_community_id, document.repository_unique_id, document.document_unique_id


def _reference_summary(identifiers: tuple[str, str, str]) -> dict:
    """Give the tuple's fields names so missing or additional references are readable."""
    return dict(zip(("home_community_id", "repository_unique_id", "document_unique_id"), identifiers))


def _document_summary(document: NIRDocumentReference) -> dict:
    """Describe the document without exposing its report or DICOM payload."""
    return {**_reference_summary(_identifiers(document)), "mime_type": document.mime_type}


async def check_sandbox(client: NIRClient, retrieve: bool = False) -> list[dict]:
    """Require all published references; report any additional sandbox documents.

    Optional ITI-39 retrieval checks the references and nonempty payloads for every
    document returned by ITI-38. It does not write patient files or print payloads.
    """
    # Keep this developer check on the open sandbox even when GP Connect uses INT.
    # Authentication and onboarding checks need their own INT requests and context.
    if client.config.environment != "sandbox":
        raise ValueError("Published test patient checks require NIR_ENV=sandbox (or dev)")

    results = []
    for nhs_number, expected_references in TEST_PATIENTS.items():
        # Start each patient as unsuccessful; only the checks below can mark it
        # successful. A failure for one patient must not stop checking the others.
        result = {"nhs_number": nhs_number, "expected_document_count": len(expected_references), "success": False}
        try:
            # ITI-38 returns metadata. Compare the community/repository/document
            # tuples so a matching UID in the wrong community cannot pass the check.
            query = await client.query_documents(nhs_number)
            actual = {_identifiers(document) for document in query.documents}
            missing = set(expected_references) - actual
            additional = actual - set(expected_references)
            # Additional documents are useful diagnostic output and are allowed.
            # Missing published documents or a SOAP/registry error fail the patient.
            result.update(
                status=query.status,
                errors=query.errors,
                document_count=len(query.documents),
                documents=[_document_summary(document) for document in query.documents],
                missing_documents=[_reference_summary(reference) for reference in sorted(missing)],
                additional_documents=[_reference_summary(reference) for reference in sorted(additional)],
                success=query.success and not missing,
            )
            if retrieve and query.success and query.documents:
                # Retrieve every discovered document, including extras. The client
                # handles community grouping; this check verifies complete payloads.
                response = await client.retrieve_documents(query.documents)
                retrieved = {_identifiers(document) for document in response.documents if document.content}
                missing_payloads = actual - retrieved
                # HTTP or registry success alone cannot prove that bytes arrived.
                # Require every discovered reference and reject empty payloads.
                retrieval_success = (
                    response.success
                    and not missing_payloads
                    and all(document.content for document in response.documents)
                )
                result["retrieval"] = {
                    "status": response.status,
                    "errors": response.errors,
                    "success": retrieval_success,
                    "documents": [
                        {**_document_summary(document), "byte_length": len(document.content)}
                        for document in response.documents
                    ],
                    "missing_documents": [_reference_summary(reference) for reference in sorted(missing_payloads)],
                }
                result["success"] = result["success"] and retrieval_success
        except (httpx.HTTPError, ValueError, ParseError) as exc:
            # Return a useful patient result for transport, validation or malformed
            # XML failures, then move on to the next published patient.
            result["success"] = False
            result["errors"] = [f"{type(exc).__name__}: {exc}"]
        results.append(result)
    return results


async def _run(retrieve: bool, health: bool) -> int:
    """Load NIR settings, run the checks and emit one JSON result per patient."""
    config = NIRConfig.from_env()
    if config.environment != "sandbox":
        raise ValueError("Published test patient checks require NIR_ENV=sandbox (or dev)")
    # Share one HTTP client across health, discovery and retrieval requests; the
    # context manager closes its connections when all patient checks finish.
    async with NIRClient(config) as client:
        if health:
            print(json.dumps({"endpoint": "health", "success": True, "result": await client.health()}))
        results = await check_sandbox(client, retrieve=retrieve)
    for result in results:
        print(json.dumps(result))
    # A nonzero exit status makes this usable in a shell script or smoke check.
    return 0 if all(result["success"] for result in results) else 1


def main(argv: list[str] | None = None) -> int:
    """Expose optional health and payload checks through ``python -m app.nir.sandbox``."""
    parser = argparse.ArgumentParser(description="Check all three NHS published NIR sandbox patients")
    parser.add_argument("--retrieve", action="store_true", help="Also retrieve every discovered document with ITI-39")
    parser.add_argument("--health", action="store_true", help="Check /health before querying patients")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args.retrieve, args.health))
    except (httpx.HTTPError, ValueError, ParseError, OSError) as exc:
        # Configuration, TLS file or health failures happen before per-patient
        # results exist, so report them once and fail the command as a whole.
        print(json.dumps({"success": False, "errors": [f"{type(exc).__name__}: {exc}"]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
