"""CPU isolation, prewarmed-cache preparation and output compatibility."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import xmltodict
from fastapi import HTTPException
from fhirclient.models.bundle import Bundle

from app.ccda import dmd
from app.ccda.conversion import ConversionFailure, ConvertedDocument, convert_document, prepare_bundle
from app.ccda.entries.medication import medication_dmd_code
from app.ccda.fhir2ccda import convert_bundle_sync, medication_entries
from app.ccda.models.dmd import DMDConcept
from app.ccda.workers import ConversionWorkers

FIXTURES = Path(__file__).parent / "fixtures" / "bundles"
OPTIONS = {"inclusions": {}, "caching_period": "24 hours", "debug_xml": False}


def test_preparation_matches_renderer_eligibility():
    # Covers aliases, structured doses, mapped free text and multiple sections
    # across the representative mixed fixtures, without terminology network I/O.
    for name in ("9690937472.json", "9692136744.json", "9692140466.json"):
        prepared = prepare_bundle((FIXTURES / name).read_bytes())
        assert not isinstance(prepared, ConversionFailure)
        model = Bundle(prepared.payload)
        index = {}
        for entry in model.entry or []:
            index[f"{entry.resource.resource_type}/{entry.resource.id}"] = entry.resource
            if entry.fullUrl:
                index[entry.fullUrl] = entry.resource
        expected = []
        for entry in medication_entries(model, index):
            try:
                code = medication_dmd_code(entry, index)
                if code is not None:
                    expected.append(code)
            except Exception:
                continue
        assert prepared.dmd_codes == list(dict.fromkeys(expected))


def test_preparation_ignores_other_sections_and_unreferenced_medications():
    payload = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": {"resourceType": "Medication", "id": "unused", "code": {"coding": [{"code": "123"}]}}},
            {
                "resource": {
                    "resourceType": "List",
                    "title": "Problems",
                    "entry": [{"item": {"reference": "Medication/unused"}}],
                }
            },
        ],
    }
    assert prepare_bundle(json.dumps(payload).encode()).dmd_codes == []


@pytest.mark.asyncio
async def test_warm_batch_deduplicates_and_pins_release(monkeypatch):
    concepts = {code: DMDConcept(concept_id=code, valueString="Synthetic") for code in (1, 2, 3)}
    cache = AsyncMock()
    cache.get.return_value = b"release-a"
    cache.mget.side_effect = [[concepts[1].model_dump_json(), concepts[2].model_dump_json()], [None]]
    resolve = AsyncMock(return_value=concepts[3])
    monkeypatch.setattr(dmd, "snomed_client", cache)
    monkeypatch.setattr(dmd, "_resolve_dmd_concept", resolve)
    result = await dmd.load_medication_dmd([1, 2, 1, 3], batch_size=2)
    assert result == concepts
    cache.get.assert_awaited_once_with(dmd.ACTIVE_RELEASE_KEY)
    assert cache.mget.await_args_list[0].args == (
        [dmd.dmd_resolved_key(1, "release-a"), dmd.dmd_resolved_key(2, "release-a")],
    )
    resolve.assert_awaited_once_with(3, release="release-a")
    cache.setex.assert_awaited_once_with(
        dmd.dmd_resolved_key(3, "release-a"), dmd.DMD_RESOLVED_TTL, concepts[3].model_dump_json()
    )


@pytest.mark.asyncio
async def test_empty_batch_has_no_io_and_failed_enrichment_is_explicit(monkeypatch):
    cache = AsyncMock()
    cache.get.return_value = None
    monkeypatch.setattr(dmd, "snomed_client", cache)
    assert await dmd.load_medication_dmd([]) == {}
    cache.get.assert_not_awaited()
    resolve = AsyncMock(side_effect=ValueError("unavailable"))
    monkeypatch.setattr(dmd, "_resolve_dmd_concept", resolve)
    assert await dmd.load_medication_dmd([1]) == {1: None}
    resolve.assert_awaited_once_with(1, release=None)


@pytest.mark.asyncio
async def test_fallback_concurrency_and_cancellation(monkeypatch):
    cache = AsyncMock()
    cache.get.return_value = None
    monkeypatch.setattr(dmd, "snomed_client", cache)
    entered = asyncio.Event()
    active = 0
    peak = 0

    async def resolve(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    monkeypatch.setattr(dmd, "_resolve_dmd_concept", resolve)
    task = asyncio.create_task(dmd.load_medication_dmd(list(range(20)), concurrency=2))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peak == 2 and active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_abandoned_jobs_hold_capacity_until_finished(monkeypatch, timeout):
    # A controlled executor exercises the real pool wrapper deterministically.
    from app.ccda import workers as module

    monkeypatch.setattr(module, "ProcessPoolExecutor", lambda **kw: ThreadPoolExecutor(max_workers=1))
    workers = ConversionWorkers(workers=1, queue_size=0, timeout=0.05 if timeout else 5)
    started = threading.Event()
    release = threading.Event()

    def job():
        started.set()
        release.wait(5)
        return 42

    task = asyncio.create_task(workers.run(job))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        if timeout:
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.status_code == 504
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(HTTPException) as error:
            await workers.run(int, 7)
        assert error.value.status_code == 503
        release.set()
        for _ in range(100):
            if not workers._pending:
                break
            await asyncio.sleep(0.01)
        assert await workers.run(int, 7) == 7
    finally:
        release.set()
        await workers.close()


@pytest.mark.asyncio
async def test_real_spawn_worker_round_trip():
    workers = ConversionWorkers(workers=1, queue_size=1, timeout=30)
    try:
        prepared = await workers.run(prepare_bundle, (FIXTURES / "9690937472.json").read_bytes())
        assert not isinstance(prepared, ConversionFailure)
        result = await workers.run(convert_document, prepared.payload, {}, OPTIONS)
        assert isinstance(result, ConvertedDocument)
        assert result.encoded and result.resource_count > 0
        assert set(result.timings) == {"fhir.models", "fhir.index", "ccda.convert", "ccda.serialize"}
        malformed = await workers.run(prepare_bundle, b"invalid json")
        assert malformed == ConversionFailure("validate_fhir_bundle.json_decode", "JSONDecodeError", True)
    finally:
        await workers.close()


@pytest.mark.parametrize("name", ["9690937472.json", "9692136744.json", "investigations/9465700088.json"])
def test_fast_serializer_preserves_xml(name, monkeypatch):
    prepared = prepare_bundle((FIXTURES / name).read_bytes())
    model = Bundle(prepared.payload)
    index = {}
    for entry in model.entry or []:
        index[f"{entry.resource.resource_type}/{entry.resource.id}"] = entry.resource
        if entry.fullUrl:
            index[entry.fullUrl] = entry.resource
    document = convert_bundle_sync(model, index, {})
    assert xmltodict._RUST_AVAILABLE, "The test environment must exercise the Rust serializer"
    fast = xmltodict.unparse(document, pretty=True)
    monkeypatch.setattr(xmltodict, "_RUST_AVAILABLE", False)
    reference = xmltodict.unparse(document, pretty=True)
    # Exact text comparison protects narrative mixed content and whitespace too.
    assert fast == reference


@pytest.mark.asyncio
async def test_cancelled_document_never_audits_or_publishes(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import httpx

    from app.ccda.conversion import PreparedBundle
    from app.gpconnect_stages import document
    from app.gpconnect_stages.types import DiagnosticStep

    entered = asyncio.Event()
    completed = asyncio.get_running_loop().create_future()

    class Workers:
        async def run(self, function, *args):
            if function is prepare_bundle:
                return PreparedBundle({}, [])
            entered.set()
            return await asyncio.shield(completed)

    audit = AsyncMock()
    cache = MagicMock()
    monkeypatch.setattr(document.audit, "attempt_audit", audit)
    monkeypatch.setattr(document, "redis_client", cache)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(conversion_workers=Workers())))
    task = asyncio.create_task(
        document.process_document(
            httpx.Response(200, content=b"{}"),
            9999999999,
            None,
            ssp_trace_id=None,
            request=request,
            log_dir=None,
            step=DiagnosticStep("start"),
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    completed.set_result(ConvertedDocument("abandoned"))
    await asyncio.sleep(0)
    audit.assert_not_awaited()
    cache.pipeline.assert_not_called()
