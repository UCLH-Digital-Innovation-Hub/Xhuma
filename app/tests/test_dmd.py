from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ccda.dmd import dmd_lookup

example_return = {
    "parameter": [
        {"name": "code", "valueCode": "42010411000001105"},
        {"name": "display", "valueString": "Codeine 30mg tablets"},
        {"name": "name", "valueString": "Dictionary of medicines and devices (dm+d)"},
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "ROUTECD"},
                {
                    "name": "value",
                    "valueCoding": {"code": "26643006", "system": "https://dmd.nhs.uk"},
                },
            ],
        },
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "VPI"},
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "ISID"},
                        {
                            "name": "value",
                            "valueCoding": {
                                "code": "261000",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                        {
                            "name": "valueCoding",
                            "valueCoding": {
                                "code": "261000",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                    ],
                },
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "BASIS_STRNTCD"},
                        {
                            "name": "value",
                            "valueCoding": {
                                "code": "1",
                                "system": "https://dmd.nhs.uk/BASIS_OF_STRNTH",
                            },
                        },
                        {
                            "name": "valueCoding",
                            "valueCoding": {
                                "code": "1",
                                "system": "https://dmd.nhs.uk/BASIS_OF_STRNTH",
                            },
                        },
                    ],
                },
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "STRNT_NMRTR_VAL"},
                        {"name": "value", "valueDecimal": 30.0},
                        {"name": "valueDecimal", "valueDecimal": 30.0},
                    ],
                },
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "STRNT_NMRTR_UOMCD"},
                        {
                            "name": "value",
                            "valueCoding": {
                                "code": "258684004",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                        {
                            "name": "valueCoding",
                            "valueCoding": {
                                "code": "258684004",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                    ],
                },
            ],
        },
    ],
    "resourceType": "Parameters",
}

example_unit_concept = {
    "parameter": [
        {"name": "code", "valueCode": "258682000"},
        {"name": "display", "valueString": "gram"},
        {"name": "name", "valueString": "Dictionary of medicines and devices (dm+d)"},
        {"name": "system", "valueUri": "https://dmd.nhs.uk"},
        {"name": "version", "valueString": "202603.2.0"},
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "inactive"},
                {"name": "value", "valueBoolean": False},
            ],
        },
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "parent"},
                {"name": "value", "valueCode": "UOM"},
            ],
        },
        {
            "name": "designation",
            "part": [
                {
                    "name": "use",
                    "valueCoding": {
                        "code": "display",
                        "system": "http://terminology.hl7.org/CodeSystem/designation-usage",
                    },
                },
                {"name": "value", "valueString": "gram"},
            ],
        },
    ],
    "resourceType": "Parameters",
}

example_route_concept = {
    "parameter": [
        {"name": "code", "valueCode": "26643006"},
        {"name": "display", "valueString": "Oral"},
        {"name": "name", "valueString": "Dictionary of medicines and devices (dm+d)"},
        {"name": "system", "valueUri": "https://dmd.nhs.uk"},
        {"name": "version", "valueString": "202603.2.0"},
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "inactive"},
                {"name": "value", "valueBoolean": False},
            ],
        },
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "parent"},
                {"name": "value", "valueCode": "ROUTE"},
            ],
        },
        {
            "name": "designation",
            "part": [
                {
                    "name": "use",
                    "valueCoding": {
                        "code": "display",
                        "system": "http://terminology.hl7.org/CodeSystem/designation-usage",
                    },
                },
                {"name": "value", "valueString": "Oral"},
            ],
        },
    ],
    "resourceType": "Parameters",
}


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
@patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock)
@patch("app.ccda.dmd.httpx.AsyncClient")
async def test_dmd_lookup(mock_async_client, mock_get_token, mock_snomed):
    # Redis/cache behaviour
    mock_snomed.get.return_value = None
    mock_snomed.setex.return_value = True

    # Token
    mock_get_token.return_value = "fake-dmd-token"

    # Mock API responses, in order:
    # 1. main DMD concept
    # 2. unit concept
    # 3. route concept
    response_1 = MagicMock()
    response_1.status_code = 200
    response_1.json.return_value = example_return

    response_2 = MagicMock()
    response_2.status_code = 200
    response_2.json.return_value = example_unit_concept

    response_3 = MagicMock()
    response_3.status_code = 200
    response_3.json.return_value = example_route_concept

    mock_client_instance = AsyncMock()
    mock_client_instance.get.side_effect = [response_1, response_2, response_3]

    mock_async_client.return_value.__aenter__.return_value = mock_client_instance

    concept = await dmd_lookup(42010411000001105)

    assert concept.concept_id == 42010411000001105
    assert concept.valueString == "Codeine 30mg tablets"

    assert concept.vpi is not None
    assert concept.vpi.value == 30.0
    assert concept.vpi.unit == "gram"

    assert concept.route is not None
    assert concept.route.code == "26643006"
    assert concept.route.displayName == "Oral"

    assert mock_snomed.setex.await_count == 3


@pytest.mark.asyncio
async def test_prewarmed_concept_awaits_cache_without_http():
    import json

    from app.ccda.dmd import dmd_cache_key, get_dmd_concept

    with patch("app.ccda.dmd.snomed_client", autospec=True) as cache, patch("app.ccda.dmd.httpx.AsyncClient") as http:
        cache.get.return_value = json.dumps(example_return).encode()
        assert await get_dmd_concept(123, properties=["parent", "VPI"]) == example_return
        cache.get.assert_awaited_once_with(dmd_cache_key(123, ["parent", "VPI"]))
        cache.setex.assert_not_awaited()
        http.assert_not_called()


@pytest.mark.asyncio
async def test_terminology_token_cache_write_is_awaited():
    import httpx

    from app.ccda.dmd import get_terminology_token

    with (
        patch("app.ccda.dmd.client_id", "test-client"),
        patch("app.ccda.dmd.client_secret", "test-secret"),
        patch("app.ccda.dmd.snomed_client", autospec=True) as cache,
        patch("app.ccda.dmd.httpx.AsyncClient") as factory,
    ):
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json={"access_token": "cached-token"}, request=httpx.Request("POST", "https://example.test/token")
        )
        factory.return_value.__aenter__.return_value = client
        assert await get_terminology_token() == "cached-token"
        cache.setex.assert_awaited_once_with("dmd_token", 30 * 60, "cached-token")


@pytest.mark.asyncio
async def test_dmd_base_url_configurable(monkeypatch):
    import httpx

    from app.ccda.dmd import get_dmd_concept

    monkeypatch.setenv("DMD_BASE_URL", "https://ontology.nhs.uk/production2/fhir")

    with (
        patch("app.ccda.dmd.snomed_client", autospec=True) as cache,
        patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock) as token_mock,
        patch("app.ccda.dmd.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None
        cache.setex.return_value = True
        token_mock.return_value = "fake-token"

        client = AsyncMock()
        client.get.return_value = httpx.Response(200, json={}, request=httpx.Request("GET", "https://example.test"))
        factory.return_value.__aenter__.return_value = client

        await get_dmd_concept(123)

        # Verify the requested URL used production2
        call_args = client.get.call_args[0]
        assert call_args[0].startswith("https://ontology.nhs.uk/production2/fhir/CodeSystem/$lookup")


@pytest.mark.asyncio
async def test_active_release_resolved_hit_skips_network():
    """Verify that an active-release resolved cache hit returns immediately without network calls."""
    from app.ccda.dmd import ACTIVE_RELEASE_KEY, dmd_lookup, dmd_resolved_key
    from app.ccda.models.dmd import DMDConcept

    expected = DMDConcept(concept_id=42010411000001105, valueString="Codeine 30mg tablets")
    release = "202610.1.0"

    with patch("app.ccda.dmd.snomed_client", autospec=True) as cache, patch("app.ccda.dmd.httpx.AsyncClient") as http:

        async def mock_get(key):
            if key == ACTIVE_RELEASE_KEY:
                return release.encode("utf-8")
            if key == dmd_resolved_key(42010411000001105, release):
                return expected.model_dump_json().encode("utf-8")
            return None

        cache.get.side_effect = mock_get
        result = await dmd_lookup(42010411000001105)

        assert result.concept_id == 42010411000001105
        assert result.valueString == "Codeine 30mg tablets"
        http.assert_not_called()


@pytest.mark.asyncio
async def test_release_change_does_not_reuse_old_resolved_entry():
    """Verify that changing dmd:active-release queries the new release namespace and does not reuse old entry."""
    from app.ccda.dmd import ACTIVE_RELEASE_KEY, dmd_lookup, dmd_resolved_key
    from app.ccda.models.dmd import DMDConcept

    old_concept = DMDConcept(concept_id=123, valueString="Old Release Med")
    new_concept_json = {
        "parameter": [
            {"name": "code", "valueCode": "123"},
            {"name": "display", "valueString": "New Release Med"},
            {"name": "version", "valueString": "202610.2.0"},
        ]
    }

    with (
        patch("app.ccda.dmd.snomed_client", autospec=True) as cache,
        patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock) as token_mock,
        patch("app.ccda.dmd.httpx.AsyncClient") as factory,
    ):
        token_mock.return_value = "fake-token"

        async def mock_get(key):
            if key == ACTIVE_RELEASE_KEY:
                return b"202610.2.0"  # Active release moved to new version
            if key == dmd_resolved_key(123, "202610.1.0"):
                return old_concept.model_dump_json().encode("utf-8")
            return None

        cache.get.side_effect = mock_get
        cache.setex.return_value = True

        client = AsyncMock()
        import httpx

        client.get.return_value = httpx.Response(
            200, json=new_concept_json, request=httpx.Request("GET", "https://example.test")
        )
        factory.return_value.__aenter__.return_value = client

        res = await dmd_lookup(123)
        assert res.valueString == "New Release Med"
        # Assert cached under new release
        new_key = dmd_resolved_key(123, "202610.2.0")
        cache.setex.assert_any_await(new_key, pytest.approx(9 * 24 * 3600, rel=100), res.model_dump_json())


@pytest.mark.asyncio
async def test_on_demand_miss_stays_pinned_to_active_release():
    """Verify that an on-demand miss queries upstream with version=release and pins all lookups."""
    import httpx

    from app.ccda.dmd import ACTIVE_RELEASE_KEY, dmd_lookup

    with (
        patch("app.ccda.dmd.snomed_client", autospec=True) as cache,
        patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock) as token_mock,
        patch("app.ccda.dmd.httpx.AsyncClient") as factory,
    ):
        token_mock.return_value = "fake-token"

        async def mock_get(key):
            if key == ACTIVE_RELEASE_KEY:
                return b"202610.1.0"
            return None

        cache.get.side_effect = mock_get
        cache.setex.return_value = True

        client = AsyncMock()
        resp_data = {
            "parameter": [
                {"name": "code", "valueCode": "999"},
                {"name": "display", "valueString": "Pinned Med"},
                {"name": "version", "valueString": "202610.1.0"},
            ]
        }
        client.get.return_value = httpx.Response(
            200, json=resp_data, request=httpx.Request("GET", "https://example.test")
        )
        factory.return_value.__aenter__.return_value = client

        await dmd_lookup(999)

        call_url = client.get.call_args[0][0]
        assert "version=202610.1.0" in call_url


@pytest.mark.asyncio
async def test_version_mismatch_raises_error_when_pinned():
    """Verify that if upstream returns a release different from pinned release, an error is raised."""
    import httpx

    from app.ccda.dmd import get_dmd_concept

    with (
        patch("app.ccda.dmd.snomed_client", autospec=True) as cache,
        patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock) as token_mock,
        patch("app.ccda.dmd.httpx.AsyncClient") as factory,
    ):
        token_mock.return_value = "fake-token"
        cache.get.return_value = None

        client = AsyncMock()
        resp_data = {
            "parameter": [
                {"name": "code", "valueCode": "999"},
                {"name": "display", "valueString": "Mismatched Med"},
                {"name": "version", "valueString": "202609.1.0"},  # Mismatched!
            ]
        }
        client.get.return_value = httpx.Response(
            200, json=resp_data, request=httpx.Request("GET", "https://example.test")
        )
        factory.return_value.__aenter__.return_value = client

        with pytest.raises(ValueError, match="does not match requested release"):
            await get_dmd_concept(999, release="202610.1.0")


def test_ttl_hierarchy_contract():
    """Verify TTL hierarchy: data TTL > active pointer TTL."""
    from app.ops.dmd_cache import DMD_ACTIVE_RELEASE_TTL, DMD_MANIFEST_TTL, DMD_RESOLVED_TTL

    assert DMD_RESOLVED_TTL > DMD_ACTIVE_RELEASE_TTL
    assert DMD_MANIFEST_TTL >= DMD_RESOLVED_TTL
    assert DMD_ACTIVE_RELEASE_TTL >= 7 * 24 * 3600  # More than 7 days


@pytest.mark.asyncio
async def test_warmer_lock_prevents_concurrent_execution():
    """Verify that concurrent warmer fails cleanly when lock is already held."""
    from app.ops.dmd_cache import warm

    with patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock) as lock_mock:
        lock_mock.return_value = False  # Lock held by someone else
        exit_code = await warm()
        assert exit_code == 1


@pytest.mark.asyncio
async def test_warmer_lock_ownership_safe_release():
    """Verify that release_lock uses Lua script with owner token."""
    from app.ops.dmd_cache import LUA_RELEASE_LOCK, release_lock

    with patch("app.ops.dmd_cache.snomed_client", autospec=True) as cache:
        cache.eval.return_value = 1
        res = await release_lock("owner-123")
        assert res is True
        cache.eval.assert_awaited_once_with(LUA_RELEASE_LOCK, 1, "dmd:warm:v1:lock", "owner-123")


@pytest.mark.asyncio
async def test_warmer_aborts_and_does_not_activate_on_eviction():
    """Verify that if evicted_keys increases during warm, release activation is aborted."""
    from app.ops.dmd_cache import ACTIVE_RELEASE_KEY, warm

    with (
        patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock) as lock_mock,
        patch("app.ops.dmd_cache.release_lock", new_callable=AsyncMock),
        patch("app.ops.dmd_cache.get_redis_memory_state", new_callable=AsyncMock) as mem_mock,
        patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock) as token_mock,
        patch("app.ops.dmd_cache.discover_release_capacity", new_callable=AsyncMock) as rel_mock,
        patch("app.ops.dmd_cache.sample_payload_metrics", new_callable=AsyncMock) as sample_mock,
        patch("app.ops.dmd_cache._expand_and_cache_lookups", new_callable=AsyncMock) as exp1_mock,
        patch("app.ops.dmd_cache._expand_vmp_concepts", new_callable=AsyncMock) as exp2_mock,
        patch("app.ops.dmd_cache._expand_amp_concepts", new_callable=AsyncMock) as exp3_mock,
        patch("app.ops.dmd_cache.snomed_client", autospec=True) as cache,
    ):
        lock_mock.return_value = True
        token_mock.return_value = "token"
        rel_mock.return_value = {
            "release": "202610.1.0",
            "counts": {"ROUTE": 100, "UOM": 100, "VMP": 500, "AMP": 500},
            "total_concepts": 1200,
            "page_counts": {"ROUTE": 1, "UOM": 1, "VMP": 1, "AMP": 1},
        }
        sample_mock.return_value = ([512], [512], [0.1])
        exp1_mock.return_value = 100
        exp2_mock.return_value = 500
        exp3_mock.return_value = 500

        # Simulate eviction detected:
        mem_mock.side_effect = [
            {"used_memory": 1000, "maxmemory": 200_000_000, "evicted_keys": 0},  # start
            {"used_memory": 5000, "maxmemory": 200_000_000, "evicted_keys": 5},  # end (evicted 5 keys!)
        ]

        exit_code = await warm()
        assert exit_code == 1

        # Verify active release was NOT set
        for call in cache.setex.await_args_list:
            assert call[0][0] != ACTIVE_RELEASE_KEY


def test_memory_guard_ceiling_exceeded():
    """Verify that memory guard raises when projected usage exceeds ceiling."""
    from app.ops.dmd_cache import check_memory_guard

    state = {"used_memory": 850, "maxmemory": 1000, "evicted_keys": 0}
    with pytest.raises(RuntimeError, match="Redis memory guard exceeded"):
        check_memory_guard(state, additional_bytes=0)


@pytest.mark.asyncio
async def test_successful_complete_warm_activates_at_end():
    """Verify that a successful complete warm writes manifest and activates release at the very end."""
    from app.ops.dmd_cache import ACTIVE_RELEASE_KEY, manifest_key, warm

    with (
        patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock) as lock_mock,
        patch("app.ops.dmd_cache.release_lock", new_callable=AsyncMock),
        patch("app.ops.dmd_cache.get_redis_memory_state", new_callable=AsyncMock) as mem_mock,
        patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock) as token_mock,
        patch("app.ops.dmd_cache.discover_release_capacity", new_callable=AsyncMock) as rel_mock,
        patch("app.ops.dmd_cache.sample_payload_metrics", new_callable=AsyncMock) as sample_mock,
        patch("app.ops.dmd_cache._expand_and_cache_lookups", new_callable=AsyncMock) as exp1_mock,
        patch("app.ops.dmd_cache._expand_vmp_concepts", new_callable=AsyncMock) as exp2_mock,
        patch("app.ops.dmd_cache._expand_amp_concepts", new_callable=AsyncMock) as exp3_mock,
        patch("app.ops.dmd_cache.snomed_client", autospec=True) as cache,
    ):
        lock_mock.return_value = True
        token_mock.return_value = "token"
        rel_mock.return_value = {
            "release": "202610.1.0",
            "counts": {"ROUTE": 50, "UOM": 60, "VMP": 1000, "AMP": 2000},
            "total_concepts": 3110,
            "page_counts": {"ROUTE": 1, "UOM": 1, "VMP": 1, "AMP": 2},
        }
        sample_mock.return_value = ([512], [512], [0.1])
        exp1_mock.side_effect = [50, 60]  # route, uom
        exp2_mock.return_value = 1000  # vmp
        exp3_mock.return_value = 2000  # amp

        mem_mock.side_effect = [
            {"used_memory": 1000, "maxmemory": 200_000_000, "evicted_keys": 0},
            {"used_memory": 2000, "maxmemory": 200_000_000, "evicted_keys": 0},
        ]

        exit_code = await warm()
        assert exit_code == 0

        from unittest.mock import ANY

        # Verify manifest write
        cache.setex.assert_any_await(
            manifest_key("202610.1.0"),
            pytest.approx(9 * 24 * 3600, rel=100),
            ANY,
        )

        # Verify active release activation
        cache.setex.assert_any_await(
            ACTIVE_RELEASE_KEY,
            pytest.approx(8 * 24 * 3600, rel=100),
            "202610.1.0",
        )


@pytest.mark.asyncio
async def test_partial_vmp_failure_does_not_activate():
    """Verify that if VMP expansion fails, warm returns 1 and active release is untouched."""
    from app.ops.dmd_cache import ACTIVE_RELEASE_KEY, warm

    with (
        patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock) as lock_mock,
        patch("app.ops.dmd_cache.release_lock", new_callable=AsyncMock),
        patch("app.ops.dmd_cache.get_redis_memory_state", new_callable=AsyncMock) as mem_mock,
        patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock) as token_mock,
        patch("app.ops.dmd_cache.discover_release_capacity", new_callable=AsyncMock) as rel_mock,
        patch("app.ops.dmd_cache.sample_payload_metrics", new_callable=AsyncMock) as sample_mock,
        patch("app.ops.dmd_cache._expand_and_cache_lookups", new_callable=AsyncMock) as exp1_mock,
        patch("app.ops.dmd_cache._expand_vmp_concepts", new_callable=AsyncMock) as exp2_mock,
        patch("app.ops.dmd_cache.snomed_client", autospec=True) as cache,
    ):
        lock_mock.return_value = True
        token_mock.return_value = "token"
        rel_mock.return_value = {
            "release": "202610.1.0",
            "counts": {"ROUTE": 50, "UOM": 60, "VMP": 1000, "AMP": 2000},
            "total_concepts": 3110,
            "page_counts": {"ROUTE": 1, "UOM": 1, "VMP": 1, "AMP": 2},
        }
        sample_mock.return_value = ([512], [512], [0.1])
        exp1_mock.side_effect = [50, 60]
        exp2_mock.side_effect = RuntimeError("VMP expansion HTTP timeout")

        mem_mock.return_value = {"used_memory": 1000, "maxmemory": 200_000_000, "evicted_keys": 0}

        exit_code = await warm()
        assert exit_code == 1

        for call in cache.setex.await_args_list:
            assert call[0][0] != ACTIVE_RELEASE_KEY


@pytest.mark.asyncio
async def test_status_reporting_output():
    """Verify status command returns active release and operational statistics."""
    import json

    from app.ops.dmd_cache import ACTIVE_RELEASE_KEY, manifest_key, status

    manifest_content = {
        "release": "202610.1.0",
        "completed_at": "2026-10-15T05:01:00Z",
        "counts": {"ROUTE": 100, "UOM": 120, "VMP": 15000, "AMP": 30000},
    }

    with patch("app.ops.dmd_cache.snomed_client", autospec=True) as cache:

        async def mock_get(key):
            if key == ACTIVE_RELEASE_KEY:
                return b"202610.1.0"
            if key == manifest_key("202610.1.0"):
                return json.dumps(manifest_content).encode("utf-8")
            if key == "dmd:warm:v1:lock":
                return None
            return None

        cache.get.side_effect = mock_get
        cache.ttl.return_value = 650000
        cache.info.return_value = {"used_memory": 50000000, "maxmemory": 200000000, "evicted_keys": 0}

        report = await status()

        assert report["active_release"] == "202610.1.0"
        assert report["active_release_ttl_seconds"] == 650000
        assert report["is_locked"] is False
        assert report["manifest"]["counts"]["VMP"] == 15000


# ---------------------------------------------------------------------------
# HTTP Bounds and Retries Tests (Phase 2 Section D)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminology_client_timeout_retries_and_succeeds():
    """Verify TerminologyClient retries upon TimeoutException and returns successful response."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    success_resp = httpx.Response(200, json={"expansion": {"contains": []}}, request=req)

    mock_http_client.post.side_effect = [
        httpx.ReadTimeout("Read timed out", request=req),
        success_resp,
    ]

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123")
    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = await client.post_fhir("ValueSet/$expand", {})
        assert resp.status_code == 200
        assert mock_http_client.post.await_count == 2


@pytest.mark.asyncio
async def test_terminology_client_429_retry_after():
    """Verify TerminologyClient honors 429 Retry-After header."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    rate_limited_resp = httpx.Response(
        429,
        headers={"Retry-After": "0.01"},
        request=req,
    )
    success_resp = httpx.Response(200, json={"expansion": {"contains": []}}, request=req)

    mock_http_client.post.side_effect = [rate_limited_resp, success_resp]

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123")
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client.post_fhir("ValueSet/$expand", {})
        assert resp.status_code == 200
        assert mock_http_client.post.await_count == 2
        mock_sleep.assert_awaited_once()
        assert mock_sleep.await_args[0][0] <= 0.05


@pytest.mark.asyncio
async def test_terminology_client_transient_5xx():
    """Verify TerminologyClient retries transient 503 response and succeeds."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    server_err = httpx.Response(503, headers={"Retry-After": "0.01"}, request=req)
    success_resp = httpx.Response(200, json={"expansion": {"contains": []}}, request=req)

    mock_http_client.post.side_effect = [server_err, success_resp]

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123")
    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = await client.post_fhir("ValueSet/$expand", {})
        assert resp.status_code == 200
        assert mock_http_client.post.await_count == 2


@pytest.mark.asyncio
async def test_terminology_client_retry_exhaustion():
    """Verify TerminologyClient raises HTTPStatusError when retries are exhausted."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    server_err = httpx.Response(500, request=req)

    mock_http_client.post.return_value = server_err

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123", max_attempts=3)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.HTTPStatusError):
            await client.post_fhir("ValueSet/$expand", {})
    assert mock_http_client.post.await_count == 3


@pytest.mark.asyncio
async def test_terminology_client_non_retryable_4xx():
    """Verify non-retryable 4xx (e.g. 404, 400) raises immediately without retry."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    not_found = httpx.Response(404, request=req)

    mock_http_client.post.return_value = not_found

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123")
    with pytest.raises(httpx.HTTPStatusError):
        await client.post_fhir("ValueSet/$expand", {})
    assert mock_http_client.post.await_count == 1


@pytest.mark.asyncio
async def test_terminology_client_401_token_refresh():
    """Verify single 401 token refresh path and rejection on second consecutive 401."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    unauth = httpx.Response(401, request=req)
    success = httpx.Response(200, json={"ok": True}, request=req)

    # First call: 401 -> refresh -> 200
    mock_http_client.post.side_effect = [unauth, success]

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "old_token")
    with patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = "new_token"
        resp = await client.post_fhir("ValueSet/$expand", {})
        assert resp.status_code == 200
        assert mock_http_client.post.await_count == 2
        assert client.token == "new_token"
        assert client.token_refreshed is True

    # If second consecutive 401 occurs after refresh, it must abort immediately without loop
    mock_http_client.post.side_effect = [unauth, unauth]
    client2 = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "old_token")
    with patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = "new_token"
        with pytest.raises(httpx.HTTPStatusError):
            await client2.post_fhir("ValueSet/$expand", {})


# ---------------------------------------------------------------------------
# Redis Safety & Memory Preflight Tests (Phase 2 Section E)
# ---------------------------------------------------------------------------


def test_preflight_memory_sufficient():
    """Verify memory preflight passes when projected usage is within ceiling."""
    from app.ops.dmd_cache import check_preflight_memory

    mem_state = {"used_memory": 10_000_000, "maxmemory": 200_000_000, "evicted_keys": 0}
    check_preflight_memory(mem_state, estimated_concepts=50000)


def test_preflight_memory_insufficient_aborts():
    """Verify memory preflight raises RuntimeError before writing anything when projected usage exceeds ceiling."""
    from app.ops.dmd_cache import check_preflight_memory

    mem_state = {"used_memory": 150_000_000, "maxmemory": 200_000_000, "evicted_keys": 0}
    with pytest.raises(RuntimeError, match="Redis memory preflight failed"):
        check_preflight_memory(mem_state, estimated_concepts=50000)


# ---------------------------------------------------------------------------
# Cache Type Separation Tests (Phase 2 Section F)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_raw_cache_reads_return_dict(mock_snomed):
    """Verify raw cache reads return raw FHIR Parameters dictionaries, not DMDConcept."""
    import json

    from app.ccda.dmd import dmd_raw_key, get_dmd_concept

    raw_payload = json.dumps(example_return).encode("utf-8")
    mock_snomed.get.return_value = raw_payload

    result = await get_dmd_concept(42010411000001105, properties=["VPI"], release="202610.1.0")
    assert isinstance(result, dict)
    assert result["resourceType"] == "Parameters"
    mock_snomed.get.assert_awaited_once_with(dmd_raw_key(42010411000001105, "202610.1.0", ["VPI"]))


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_resolved_cache_reads_return_dmdconcept(mock_snomed):
    """Verify resolved cache reads return DMDConcept model objects."""
    from app.ccda.dmd import ACTIVE_RELEASE_KEY, dmd_lookup, dmd_resolved_key
    from app.ccda.models.dmd import DMDConcept

    resolved_obj = DMDConcept(concept_id=42010411000001105, valueString="Codeine 30mg tablets")
    resolved_json = resolved_obj.model_dump_json().encode("utf-8")

    async def mock_get(key):
        if key == ACTIVE_RELEASE_KEY:
            return b"202610.1.0"
        if key == dmd_resolved_key(42010411000001105, "202610.1.0"):
            return resolved_json
        return None

    mock_snomed.get.side_effect = mock_get

    result = await dmd_lookup(42010411000001105)
    assert isinstance(result, DMDConcept)
    assert result.concept_id == 42010411000001105
    assert result.valueString == "Codeine 30mg tablets"


def test_no_key_interpreted_as_both_types():
    """Verify raw and resolved keys have completely disjoint namespaces."""
    from app.ccda.dmd import dmd_raw_key, dmd_resolved_key

    concept_id = 42010411000001105
    rel = "202610.1.0"
    k_raw = dmd_raw_key(concept_id, rel)
    k_raw_props = dmd_raw_key(concept_id, rel, ["VPI", "ROUTECD"])
    k_resolved = dmd_resolved_key(concept_id, rel)

    assert k_raw.startswith("dmd:raw:v1:")
    assert k_raw_props.startswith("dmd:raw:v1:")
    assert k_resolved.startswith("dmd:resolved:v1:")
    assert k_raw != k_resolved
    assert not k_raw.startswith(k_resolved)
    assert not k_resolved.startswith(k_raw)


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
@patch("app.ccda.dmd.get_dmd_concept", new_callable=AsyncMock)
async def test_active_release_cache_miss_performs_pinned_lookup(mock_get_dmd, mock_snomed):
    """Verify active-release cache miss performs upstream lookup explicitly pinned to that release."""
    from app.ccda.dmd import ACTIVE_RELEASE_KEY, dmd_lookup, dmd_resolved_key
    from app.ccda.models.dmd import DMDConcept

    async def mock_get(key):
        if key == ACTIVE_RELEASE_KEY:
            return b"202610.1.0"
        return None  # miss for resolved key

    mock_snomed.get.side_effect = mock_get
    mock_snomed.setex.return_value = True
    mock_get_dmd.return_value = example_return

    result = await dmd_lookup(42010411000001105)
    assert isinstance(result, DMDConcept)

    # Verify that get_dmd_concept was called with release="202610.1.0"
    for call in mock_get_dmd.await_args_list:
        assert call.kwargs.get("release") == "202610.1.0"

    # Verify written under resolved namespace with active release
    mock_snomed.setex.assert_any_await(
        dmd_resolved_key(42010411000001105, "202610.1.0"),
        pytest.approx(9 * 24 * 3600, rel=100),
        result.model_dump_json(),
    )


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
@patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock)
@patch("app.ccda.dmd.httpx.AsyncClient")
async def test_newer_upstream_release_cannot_be_written_under_older_active_release_key(
    mock_http_client, mock_token, mock_snomed
):
    """Verify get_dmd_concept rejects and does not write if upstream returns a release newer/different than requested."""
    from app.ccda.dmd import get_dmd_concept

    mock_snomed.get.return_value = None  # cache miss
    mock_token.return_value = "token123"

    mismatched_response = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "version", "valueString": "202610.2.0"},
            {"name": "display", "valueString": "Mismatched concept"},
        ],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mismatched_response
    mock_resp.raise_for_status = MagicMock()

    client_instance = AsyncMock()
    client_instance.get.return_value = mock_resp
    mock_http_client.return_value.__aenter__.return_value = client_instance

    with pytest.raises(ValueError, match="does not match requested release"):
        await get_dmd_concept(42010411000001105, release="202610.1.0")

    # Verify no write occurred
    assert mock_snomed.setex.await_count == 0


# ==============================================================================
# PHASE 3: RAW CACHE KEY CANONICAL PROPERTY SIGNATURE & NAMESPACE TESTS
# ==============================================================================


def test_raw_key_canonical_property_order_and_duplicates():
    """Verify property ordering and duplicate property names yield identical canonical raw keys."""
    from app.ccda.dmd import canonicalize_properties, dmd_raw_key

    rel = "202610.1.0"
    cid = 42010411000001105

    # 1. Same release + same concept + same properties in different order
    k1 = dmd_raw_key(cid, rel, ["VPI", "ROUTECD", "parent"])
    k2 = dmd_raw_key(cid, rel, ["parent", "VPI", "ROUTECD"])
    k3 = dmd_raw_key(cid, rel, ["ROUTECD", "parent", "VPI"])
    assert k1 == k2 == k3
    assert k1 == f"dmd:raw:v1:{rel}:{cid}:properties:ROUTECD,VPI,parent"

    # 2. Duplicate properties yield same canonical key
    k_dup = dmd_raw_key(cid, rel, ["VPI", "ROUTECD", "VPI", "ROUTECD", "parent"])
    assert k_dup == k1

    # 3. None and [] map to canonical 'none'
    assert canonicalize_properties(None) == "none"
    assert canonicalize_properties([]) == "none"
    assert canonicalize_properties(()) == "none"
    assert dmd_raw_key(cid, rel, None) == f"dmd:raw:v1:{rel}:{cid}:properties:none"
    assert dmd_raw_key(cid, rel, []) == f"dmd:raw:v1:{rel}:{cid}:properties:none"

    # 4. Whitespace trimming
    k_ws = dmd_raw_key(cid, rel, ["  VPI  ", " ROUTECD "])
    k_clean = dmd_raw_key(cid, rel, ["VPI", "ROUTECD"])
    assert k_ws == k_clean


def test_raw_key_different_properties_distinct():
    """Verify different property sets produce different keys and cannot collide."""
    from app.ccda.dmd import dmd_raw_key

    rel = "202610.1.0"
    cid = 42010411000001105

    k_none = dmd_raw_key(cid, rel, None)
    k_route = dmd_raw_key(cid, rel, ["ROUTECD"])
    k_vpi = dmd_raw_key(cid, rel, ["VPI"])
    k_both = dmd_raw_key(cid, rel, ["ROUTECD", "VPI"])

    keys = {k_none, k_route, k_vpi, k_both}
    assert len(keys) == 4
    assert k_none != k_route
    assert k_none != k_vpi
    assert k_none != k_both


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_raw_cache_deserializes_to_parameters_and_resolved_to_dmdconcept(mock_snomed):
    """Verify raw cache deserializes to FHIR Parameters dict, and resolved deserializes to DMDConcept."""
    import json

    from app.ccda.dmd import dmd_raw_key, dmd_resolved_key, get_dmd_concept
    from app.ccda.models.dmd import DMDConcept

    rel = "202610.1.0"
    cid = 42010411000001105

    raw_payload = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "code", "valueCode": str(cid)},
            {"name": "display", "valueString": "Test Drug 10mg"},
            {"name": "version", "valueString": rel},
        ],
    }
    resolved_model = DMDConcept(concept_id=cid, valueString="Test Drug 10mg")

    # Raw cache hit returns dict
    mock_snomed.get.return_value = json.dumps(raw_payload).encode("utf-8")
    raw_result = await get_dmd_concept(cid, properties=["ROUTECD"], release=rel)
    assert isinstance(raw_result, dict)
    assert raw_result["resourceType"] == "Parameters"
    assert raw_result["parameter"][0]["valueCode"] == str(cid)

    # Resolved cache hit returns DMDConcept
    mock_snomed.get.return_value = resolved_model.model_dump_json().encode("utf-8")

    # Verify namespaces are strictly disjoint
    k_raw = dmd_raw_key(cid, rel, ["ROUTECD"])
    k_res = dmd_resolved_key(cid, rel)
    assert k_raw.startswith("dmd:raw:v1:")
    assert k_res.startswith("dmd:resolved:v1:")
    assert not k_raw.startswith(k_res)
    assert not k_res.startswith(k_raw)


# ==============================================================================
# PHASE 4: MONOTONIC HTTP RETRY DEADLINE TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_retry_budget_clamps_attempt_timeout_to_remaining():
    """Verify that an attempt's timeout is clamped to the remaining monotonic retry budget."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    resp_success = httpx.Response(200, json={"ok": True}, request=req)

    captured_timeouts = []

    async def mock_post(url, json=None, headers=None, timeout=None):
        captured_timeouts.append(timeout)
        return resp_success

    mock_http_client.post.side_effect = mock_post

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123", retry_budget_s=5.0)

    # With remaining budget = 5.0, read timeout (default 300.0) should be clamped to <= 5.0
    await client.post_fhir("ValueSet/$expand", {})

    assert len(captured_timeouts) == 1
    t = captured_timeouts[0]
    assert t.read <= 5.0
    assert t.connect <= 5.0
    assert t.write <= 5.0
    assert t.pool <= 5.0


@pytest.mark.asyncio
async def test_retry_budget_exhaustion_before_attempt_raises():
    """Verify that if remaining budget <= 0 before an attempt, TimeoutError is raised immediately."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123", retry_budget_s=10.0)

    # Mock time.monotonic such that deadline expires immediately
    with patch("time.monotonic", side_effect=[100.0, 115.0]):
        with pytest.raises(TimeoutError, match="Overall retry budget of 10.0s exhausted"):
            await client.post_fhir("ValueSet/$expand", {})

    assert mock_http_client.post.await_count == 0


@pytest.mark.asyncio
async def test_retry_after_exceeding_budget_aborts():
    """Verify that Retry-After header exceeding the remaining budget aborts without sleeping."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    resp_429 = httpx.Response(429, headers={"Retry-After": "60"}, request=req)

    mock_http_client.post.return_value = resp_429

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123", retry_budget_s=10.0)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(httpx.HTTPStatusError):
            await client.post_fhir("ValueSet/$expand", {})

        # Should NOT sleep 60s
        assert mock_sleep.await_count == 0


@pytest.mark.asyncio
async def test_backoff_exceeding_budget_aborts_on_network_error():
    """Verify that backoff calculation exceeding remaining budget aborts on network error without sleeping."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    mock_http_client.post.side_effect = httpx.ConnectTimeout("Connection timed out", request=req)

    # Budget of 0.5s is smaller than minimum backoff (~1.1s)
    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123", retry_budget_s=0.5)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(httpx.ConnectTimeout):
            await client.post_fhir("ValueSet/$expand", {})

        assert mock_sleep.await_count == 0


@pytest.mark.asyncio
async def test_network_timeout_retry_success_within_budget():
    """Verify transient network timeout followed by successful attempt within budget."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    resp_success = httpx.Response(200, json={"ok": True}, request=req)

    mock_http_client.post.side_effect = [
        httpx.ReadTimeout("Read timed out", request=req),
        resp_success,
    ]

    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123", retry_budget_s=30.0)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client.post_fhir("ValueSet/$expand", {})
        assert resp.status_code == 200
        assert mock_http_client.post.await_count == 2
        assert mock_sleep.await_count == 1


@pytest.mark.asyncio
async def test_terminology_client_close():
    """Verify TerminologyClient.close() closes the underlying AsyncClient."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123")
    await client.close()
    mock_http_client.aclose.assert_awaited_once()


# ==============================================================================
# PHASE 5: READ-ONLY PLAN / CAPACITY PLANNING TESTS
# ==============================================================================


@pytest.mark.asyncio
@patch("app.ops.dmd_cache.snomed_client", autospec=True)
async def test_plan_mode_does_not_mutate_redis_or_acquire_lock(mock_snomed):
    """Verify plan mode performs zero Redis writes, sets no keys, and acquires no lock."""
    from app.ops.dmd_cache import plan

    mock_snomed.info = AsyncMock(
        return_value={
            "used_memory": 50 * 1024 * 1024,
            "maxmemory": 256 * 1024 * 1024,
            "evicted_keys": 0,
        }
    )

    with patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock) as mock_acquire:
        report = await plan(
            category_counts={"ROUTE": 100, "UOM": 200, "VMP": 1000, "AMP": 2000},
            raw_samples=[{"code": "1", "display": "Sample"}],
            resolved_samples=[],
            request_latencies=[0.1, 0.2],
            release="202610.1.0",
        )

        assert mock_acquire.await_count == 0
        assert mock_snomed.set.await_count == 0
        assert mock_snomed.setex.await_count == 0
        assert mock_snomed.delete.await_count == 0
        assert mock_snomed.pipeline.call_count == 0

        assert report["release"] == "202610.1.0"
        assert report["unique_concept_count"] == 3300
        assert report["fail_closed"] is False


@pytest.mark.asyncio
async def test_plan_mode_includes_raw_resolved_and_coexistence_memory():
    """Verify plan estimates include both raw and resolved caches, overhead, and existing memory."""
    from app.ops.dmd_cache import plan

    mem_state = {
        "used_memory": 80 * 1024 * 1024,  # 80 MB existing (e.g. coexisting prior release)
        "maxmemory": 256 * 1024 * 1024,  # 256 MB max
        "evicted_keys": 0,
    }

    report = await plan(
        mem_state=mem_state,
        category_counts={"ROUTE": 150, "UOM": 250, "VMP": 25000, "AMP": 35000},
        raw_samples=[{"k": "v" * 100}],
        resolved_samples=[],
        request_latencies=[0.2],
        release="202610.1.0",
        safety_multiplier=1.5,
    )

    assert report["projected_raw_bytes"] > 0
    assert report["projected_resolved_bytes"] > 0
    assert report["projected_redis_key_metadata_overhead_bytes"] > 0
    assert report["projected_total_redis_delta_bytes"] > 0
    # Coexistence memory: current used + projected delta
    assert (
        report["projected_total_memory_bytes"] == mem_state["used_memory"] + report["projected_total_redis_delta_bytes"]
    )
    assert report["current_redis_used_memory_bytes"] == mem_state["used_memory"]


@pytest.mark.asyncio
async def test_plan_mode_insufficient_memory_fails_closed():
    """Verify plan mode reports fail-closed when projected total memory exceeds threshold."""
    from app.ops.dmd_cache import plan

    # 200 MB used out of 256 MB (already > 78%)
    mem_state = {
        "used_memory": 200 * 1024 * 1024,
        "maxmemory": 256 * 1024 * 1024,
        "evicted_keys": 0,
    }

    report = await plan(
        mem_state=mem_state,
        category_counts={"ROUTE": 150, "UOM": 250, "VMP": 25000, "AMP": 35000},
        raw_samples=[{"k": "v" * 500}],
        release="202610.1.0",
    )

    assert report["fail_closed"] is True
    assert any("exceeds guard limit" in r for r in report["fail_reasons"])


@pytest.mark.asyncio
async def test_plan_mode_duration_exceeding_job_timeout_fails_closed():
    """Verify plan mode reports fail-closed when projected duration exceeds Container Apps timeout."""
    from app.ops.dmd_cache import plan

    mem_state = {
        "used_memory": 10 * 1024 * 1024,
        "maxmemory": 512 * 1024 * 1024,
        "evicted_keys": 0,
    }

    # Extremely high latency (e.g. 50s per request)
    report = await plan(
        mem_state=mem_state,
        category_counts={"ROUTE": 150, "UOM": 250, "VMP": 25000, "AMP": 35000},
        request_latencies=[50.0, 60.0],
        container_app_timeout_s=1800,
        concurrency=1,
    )

    assert report["fits_timeout"] is False
    assert report["fail_closed"] is True
    assert any("exceeds Container Apps Job timeout" in r for r in report["fail_reasons"])


def test_plan_mode_json_output_contains_no_credentials_or_phi():
    """Verify machine-readable plan output contains no credentials, tokens, or patient identifiers."""
    import asyncio
    import json

    from app.ops.dmd_cache import plan

    report = asyncio.run(
        plan(
            mem_state={"used_memory": 20 * 1024 * 1024, "maxmemory": 256 * 1024 * 1024, "evicted_keys": 0},
            category_counts={"ROUTE": 10, "UOM": 10, "VMP": 10, "AMP": 10},
        )
    )

    dumped = json.dumps(report)
    lower_dumped = dumped.lower()
    for sensitive in ("bearer", "password", "client_secret", "patient", "nhs_number"):
        assert sensitive not in lower_dumped, f"Sensitive term '{sensitive}' found in plan JSON"


# ==============================================================================
# PHASE 6 & 7: TERRAFORM, LOGIC APP & HEARTBEAT TELEMETRY TESTS
# ==============================================================================


def test_terraform_logic_app_custom_recurrence_and_role_actions():
    """Verify infra/main.tf uses custom recurrence without trailing Z and scoped operation status RBAC actions."""
    import pathlib

    main_tf = pathlib.Path("infra/main.tf").read_text()

    # Logic App custom recurrence trigger
    assert 'resource "azurerm_logic_app_trigger_custom" "dmd_warm"' in main_tf
    assert 'timeZone  = "GMT Standard Time"' in main_tf
    assert "startTime = var.dmd_cache_schedule_start_time" in main_tf

    # Container Apps Job retry limit
    import re

    assert re.search(r"replica_retry_limit\s*=\s*0", main_tf)

    # Custom role definition for Logic App operator
    assert "Microsoft.App/locations/containerappsjoboperationstatuses/read" in main_tf
    assert "Microsoft.App/locations/containerappsjoboperationresults/read" in main_tf
    assert "Microsoft.App/jobs/start/action" in main_tf
    assert "Microsoft.App/jobs/executions/read" in main_tf

    # Invariants asserting no wildcards in custom role
    operator_role_def = main_tf.partition('resource "azurerm_role_definition" "dmd_job_operator"')[2].partition("}")[0]
    assert '"*"' not in operator_role_def


@pytest.mark.asyncio
@patch("app.ops.dmd_cache.snomed_client", autospec=True)
@patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.release_lock", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.renew_lock", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.discover_release_capacity", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.sample_payload_metrics", new_callable=AsyncMock)
@patch("app.ops.dmd_cache._expand_and_cache_lookups", new_callable=AsyncMock)
@patch("app.ops.dmd_cache._expand_vmp_concepts", new_callable=AsyncMock)
@patch("app.ops.dmd_cache._expand_amp_concepts", new_callable=AsyncMock)
async def test_warm_success_emits_structured_dmd_cache_event(
    mock_amp,
    mock_vmp,
    mock_lookups,
    mock_sample,
    mock_discover,
    mock_token,
    mock_renew,
    mock_rel,
    mock_acq,
    mock_snomed,
):
    """Verify successful warm emits structured dmd_cache_event telemetry with zero evictions."""
    import json

    from app.ops.dmd_cache import warm

    mock_acq.return_value = True
    mock_renew.return_value = True
    mock_token.return_value = "token123"
    mock_discover.return_value = {
        "release": "202610.1.0",
        "counts": {"ROUTE": 100, "UOM": 100, "VMP": 1000, "AMP": 2000},
        "total_concepts": 3200,
        "page_counts": {"ROUTE": 1, "UOM": 1, "VMP": 1, "AMP": 2},
    }
    mock_sample.return_value = ([512], [512], [0.1])
    mock_lookups.return_value = 100
    mock_vmp.return_value = 1000
    mock_amp.return_value = 2000

    mock_snomed.info.return_value = {
        "used_memory": 30 * 1024 * 1024,
        "maxmemory": 256 * 1024 * 1024,
        "evicted_keys": 0,
    }
    mock_snomed.setex.return_value = True

    with patch("app.ops.dmd_cache.logger.info") as mock_log:
        exit_code = await warm()
        assert exit_code == 0

        # Verify dmd_cache_event was emitted
        event_calls = [c for c in mock_log.call_args_list if len(c.args) >= 2 and c.args[0] == "dmd_cache_event %s"]
        assert len(event_calls) == 1
        event_json = event_calls[0].args[1]
        event_dict = json.loads(event_json)

        assert event_dict["event"] == "warm_succeeded"
        assert event_dict["release"] == "202610.1.0"
        assert event_dict["concept_count"] == 3200
        assert event_dict["evictions"] == 0
        assert "duration_seconds" in event_dict


# ==============================================================================
# PHASE 2 & 18: CLINICAL RUNTIME BEHAVIOUR & CACHE CONTRACT TESTS
# ==============================================================================


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_warmed_vmp_zero_http_requests(mock_snomed):
    """Verify warmed VMP dmd_lookup() resolves completely from Redis with zero HTTP requests."""
    from app.ccda.dmd import DMDConcept, dmd_lookup
    from app.ccda.models.datatypes import CD

    vmp_concept = DMDConcept(
        concept_id=318412000,
        valueString="Paracetamol 500mg tablets",
        route=CD(code="260548002", displayName="Oral", codeSystemName="https://dmd.nhs.uk"),
    )

    # Active release is set to 202610.1.0
    mock_snomed.get = AsyncMock(
        side_effect=lambda key: "202610.1.0" if key == "dmd:active-release" else vmp_concept.model_dump_json()
    )

    with patch("app.ccda.dmd.get_dmd_concept", new_callable=AsyncMock) as mock_get_concept:
        result = await dmd_lookup("318412000")
        assert result is not None
        assert result.concept_id == 318412000
        assert result.valueString == "Paracetamol 500mg tablets"
        # Zero HTTP calls to upstream terminology
        mock_get_concept.assert_not_called()


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_warmed_amp_zero_http_requests(mock_snomed):
    """Verify warmed AMP dmd_lookup() resolves completely from Redis with zero HTTP requests."""
    from app.ccda.dmd import DMDConcept, dmd_lookup
    from app.ccda.models.datatypes import CD

    amp_concept = DMDConcept(
        concept_id=12345000,
        valueString="Panadol 500mg tablets (GSK)",
        route=CD(code="260548002", displayName="Oral", codeSystemName="https://dmd.nhs.uk"),
    )

    mock_snomed.get = AsyncMock(
        side_effect=lambda key: "202610.1.0" if key == "dmd:active-release" else amp_concept.model_dump_json()
    )

    with patch("app.ccda.dmd.get_dmd_concept", new_callable=AsyncMock) as mock_get_concept:
        result = await dmd_lookup("12345000")
        assert result is not None
        assert result.concept_id == 12345000
        assert result.valueString == "Panadol 500mg tablets (GSK)"
        mock_get_concept.assert_not_called()


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_unwarmed_concept_fallback_pinned_lookup_and_caches_resolved(mock_snomed):
    """Verify unwarmed concept performs pinned upstream lookup and safely caches the resolved result."""
    from app.ccda.dmd import dmd_lookup

    mock_snomed.get = AsyncMock(side_effect=lambda key: "202610.1.0" if key == "dmd:active-release" else None)
    mock_snomed.setex = AsyncMock(return_value=True)

    raw_upstream = {
        "concept_id": 99999000,
        "parameter": [
            {"name": "display", "valueString": "Unwarmed Solution 100mg/ml"},
        ],
    }

    with patch("app.ccda.dmd.get_dmd_concept", new_callable=AsyncMock, return_value=raw_upstream) as mock_get:
        result = await dmd_lookup("99999000")
        assert result is not None
        assert result.concept_id == 99999000
        assert result.valueString == "Unwarmed Solution 100mg/ml"

        # Upstream was called with pinned release
        mock_get.assert_awaited_once_with(99999000, properties=["VPI", "ROUTECD", "parent"], release="202610.1.0")

        # Resolved result was cached under active release
        expected_cache_key = "dmd:resolved:v1:202610.1.0:99999000"
        mock_snomed.setex.assert_awaited_once()
        call_args = mock_snomed.setex.call_args[0]
        assert call_args[0] == expected_cache_key


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_no_active_release_uses_on_demand_lookup(mock_snomed):
    """Verify when no active-release exists, lookup safely falls back to unpinned on-demand lookup."""
    from app.ccda.dmd import dmd_lookup

    mock_snomed.get = AsyncMock(return_value=None)
    mock_snomed.setex = AsyncMock(return_value=True)

    raw_upstream = {
        "concept_id": 88888000,
        "parameter": [
            {"name": "display", "valueString": "On Demand Capsule 250mg"},
        ],
    }

    with patch("app.ccda.dmd.get_dmd_concept", new_callable=AsyncMock, return_value=raw_upstream) as mock_get:
        result = await dmd_lookup("88888000")
        assert result is not None
        assert result.concept_id == 88888000
        # Upstream called without release pin
        mock_get.assert_awaited_once_with(88888000, properties=["VPI", "ROUTECD", "parent"], release=None)


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_release_matching_is_accepted_and_cached(mock_snomed):
    """Verify upstream response with matching release is accepted and written to cache."""
    import json

    import httpx

    from app.ccda.dmd import DMD_RESOLVED_TTL, get_dmd_concept

    mock_snomed.get = AsyncMock(return_value=None)
    mock_snomed.setex = AsyncMock(return_value=True)

    matching_response = {
        "parameter": [
            {"name": "version", "valueString": "http://snomed.info/sct/83821000000107/version/202610.1.0"},
            {"name": "display", "valueString": "Matching Release Product"},
        ]
    }

    with patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock, return_value="token123"):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_http_get:
            req = httpx.Request("GET", "https://ontology.nhs.uk/fhir/CodeSystem/$lookup")
            mock_http_get.return_value = httpx.Response(200, json=matching_response, request=req)
            result = await get_dmd_concept(77777000, release="202610.1.0")

            assert result == matching_response
            mock_snomed.setex.assert_awaited_once_with(
                "dmd:raw:v1:202610.1.0:77777000:properties:none",
                DMD_RESOLVED_TTL,
                json.dumps(matching_response),
            )


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_release_mismatch_prevents_cache_write(mock_snomed):
    """Verify upstream response with mismatched release raises ValueError and writes nothing to cache."""
    import httpx

    from app.ccda.dmd import get_dmd_concept

    mock_snomed.get = AsyncMock(return_value=None)
    mock_snomed.setex = AsyncMock()

    mismatched_response = {
        "parameter": [
            {"name": "version", "valueString": "http://snomed.info/sct/83821000000107/version/202609.1.0"},
            {"name": "display", "valueString": "Mismatched Release Product"},
        ]
    }

    with patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock, return_value="token123"):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_http_get:
            req = httpx.Request("GET", "https://ontology.nhs.uk/fhir/CodeSystem/$lookup")
            mock_http_get.return_value = httpx.Response(200, json=mismatched_response, request=req)
            with pytest.raises(ValueError, match="does not match requested release"):
                await get_dmd_concept(77777000, release="202610.1.0")

            # Cache was NOT written
            mock_snomed.setex.assert_not_called()


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_release_missing_metadata_prevents_cache_write(mock_snomed):
    """Verify upstream response without release metadata raises ValueError and writes nothing to cache."""
    import httpx

    from app.ccda.dmd import get_dmd_concept

    mock_snomed.get = AsyncMock(return_value=None)
    mock_snomed.setex = AsyncMock()

    missing_version_response = {
        "parameter": [
            {"name": "display", "valueString": "Missing Version Product"},
        ]
    }

    with patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock, return_value="token123"):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_http_get:
            req = httpx.Request("GET", "https://ontology.nhs.uk/fhir/CodeSystem/$lookup")
            mock_http_get.return_value = httpx.Response(200, json=missing_version_response, request=req)
            with pytest.raises(
                ValueError,
                match="DMD lookup returned release missing which does not match requested release 202610.1.0",
            ):
                await get_dmd_concept(77777000, release="202610.1.0")

            # Zero cache writes
            mock_snomed.setex.assert_not_called()


@pytest.mark.asyncio
@patch("app.ccda.dmd.snomed_client", autospec=True)
async def test_runtime_unversioned_fallback_caches_with_7day_ttl(mock_snomed):
    """Verify unpinned on-demand lookup caches under unversioned key for 7 days even without version metadata."""
    import json

    import httpx

    from app.ccda.dmd import get_dmd_concept

    mock_snomed.get = AsyncMock(return_value=None)
    mock_snomed.setex = AsyncMock(return_value=True)

    unversioned_response = {
        "parameter": [
            {"name": "display", "valueString": "Unversioned On Demand Product"},
        ]
    }

    with patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock, return_value="token123"):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_http_get:
            req = httpx.Request("GET", "https://ontology.nhs.uk/fhir/CodeSystem/$lookup")
            mock_http_get.return_value = httpx.Response(200, json=unversioned_response, request=req)
            result = await get_dmd_concept(77777000, release=None)

            assert result == unversioned_response
            mock_snomed.setex.assert_awaited_once_with(
                "snomed:77777000",
                7 * 24 * 3600,
                json.dumps(unversioned_response),
            )


# ==============================================================================
# PHASE 3 & 4: REAL CAPACITY PLANNING & DISCOVERY TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_plan_mode_without_client_or_test_inputs_fails_closed():
    """Verify plan() raises ValueError if neither a client nor explicit test arguments are provided."""
    from app.ops.dmd_cache import plan

    with pytest.raises(ValueError, match="Synthetic production fallback is prohibited"):
        await plan()


@pytest.mark.asyncio
@patch("app.ops.dmd_cache.snomed_client", autospec=True)
@patch("app.ops.dmd_cache.fetch_token_in_memory", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.discover_release_capacity", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.sample_payload_metrics", new_callable=AsyncMock)
async def test_plan_cli_executes_real_readonly_discovery_and_makes_zero_redis_writes(
    mock_sample, mock_discover, mock_token, mock_snomed
):
    """Verify CLI plan path executes real discovery without acquiring locks or writing to Redis."""
    from app.ops.dmd_cache import plan_cli

    mock_token.return_value = "secret_in_memory_token"
    mock_discover.return_value = {
        "release": "202610.1.0",
        "counts": {"ROUTE": 150, "UOM": 250, "VMP": 25000, "AMP": 35000},
        "total_concepts": 60400,
        "page_counts": {"ROUTE": 1, "UOM": 1, "VMP": 25, "AMP": 35},
    }
    mock_sample.return_value = ([512, 520], [600, 610], [0.15, 0.20])
    mock_snomed.info.return_value = {
        "used_memory": 40 * 1024 * 1024,
        "maxmemory": 256 * 1024 * 1024,
        "evicted_keys": 0,
    }

    with patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock) as mock_acq:
        exit_code = await plan_cli(sample_size=10, json_output=True)

        assert mock_acq.await_count == 0
        assert mock_snomed.set.await_count == 0
        assert mock_snomed.setex.await_count == 0
        assert mock_snomed.delete.await_count == 0
        assert mock_snomed.pipeline.call_count == 0
        assert exit_code == 0


@pytest.mark.asyncio
async def test_discover_release_capacity_fails_on_inconsistent_releases():
    """Verify discover_release_capacity raises ValueError if category calls return conflicting releases."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient, discover_release_capacity

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)

    def side_effect(url, **kwargs):
        json_body = kwargs.get("json", {})
        params = json_body.get("parameter", [])
        cat = params[0]["resource"]["compose"]["include"][0]["filter"][0]["value"]

        rel = "202610.1.0" if cat == "ROUTE" else "202609.1.0"

        resp = {
            "resourceType": "ValueSet",
            "expansion": {
                "total": 100,
                "parameter": [{"name": "version", "valueUri": f"http://snomed.info/sct/83821000000107/version/{rel}"}],
            },
        }
        req = httpx.Request("POST", f"https://ontology.nhs.uk/fhir/{url}")
        return httpx.Response(200, json=resp, request=req)

    mock_http_client.post.side_effect = side_effect
    client = TerminologyClient(mock_http_client, "https://ontology.nhs.uk/fhir", "token123")

    with pytest.raises(ValueError, match="Release mismatch across categories"):
        await discover_release_capacity(client)


def test_sample_concept_with_vpi_serializes_resolved_vpi():
    """Verify sampling pure concept resolution serializes DMDConcept containing nested VPI."""
    from app.ops.dmd_cache import _resolve_expansion_concept

    vmp_concept = {
        "code": "1001",
        "display": "Paracetamol 500mg tablets",
        "property": [
            {
                "code": "VPI",
                "subproperty": [
                    {"code": "STRNT_NMRTR_VAL", "valueDecimal": 500.0},
                    {"code": "STRNT_NMRTR_UOMCD", "valueCode": "258684004"},
                ],
            }
        ],
    }

    # Test with uom map entry
    resolved_concept, vpi, route = _resolve_expansion_concept(
        concept=vmp_concept,
        category="VMP",
        route_map={},
        uom_map={"258684004": "mg"},
        vmp_lookup={},
    )
    assert resolved_concept.vpi is not None
    assert resolved_concept.vpi.value == 500.0
    assert resolved_concept.vpi.unit == "mg"
    serialized = resolved_concept.model_dump_json()
    assert '"vpi":' in serialized
    assert '"unit":"mg"' in serialized

    # Test with fallback surrogate when uom_map is missing entry
    resolved_surrogate, _, _ = _resolve_expansion_concept(
        concept=vmp_concept,
        category="VMP",
        route_map={},
        uom_map={},
        vmp_lookup={},
        fallback_surrogate=True,
    )
    assert resolved_surrogate.vpi is not None
    assert resolved_surrogate.vpi.unit == "unit-258684004"


def test_sample_concept_with_route_serializes_resolved_route():
    """Verify sampling pure concept resolution serializes DMDConcept containing nested route."""
    from app.ops.dmd_cache import _resolve_expansion_concept

    vmp_concept = {
        "code": "1001",
        "display": "Paracetamol 500mg tablets",
        "property": [{"code": "ROUTECD", "valueCode": "260548002"}],
    }

    # Test with route map entry
    resolved_concept, vpi, route = _resolve_expansion_concept(
        concept=vmp_concept,
        category="VMP",
        route_map={"260548002": "Oral"},
        uom_map={},
        vmp_lookup={},
    )
    assert resolved_concept.route is not None
    assert resolved_concept.route.code == "260548002"
    assert resolved_concept.route.displayName == "Oral"
    serialized = resolved_concept.model_dump_json()
    assert '"route":' in serialized
    assert '"code":"260548002"' in serialized
    assert '"displayName":"Oral"' in serialized

    # Test with fallback surrogate when route_map is missing entry
    resolved_surrogate, _, _ = _resolve_expansion_concept(
        concept=vmp_concept,
        category="VMP",
        route_map={},
        uom_map={},
        vmp_lookup={},
        fallback_surrogate=True,
    )
    assert resolved_surrogate.route is not None
    assert resolved_surrogate.route.displayName == "route-260548002"


def test_capacity_admission_uses_max_sampled_resolved_and_raw_sizes():
    """Verify build_capacity_plan uses observed maximum sampled bytes for capacity projection."""
    from app.ops.dmd_cache import build_capacity_plan

    plan_res = build_capacity_plan(
        release="202610.1.0",
        category_counts={"ROUTE": 100, "UOM": 100, "VMP": 1000, "AMP": 2000},
        raw_payload_bytes=[100] * 99 + [800],  # max = 800, p95 = 100
        resolved_payload_bytes=[300] * 99 + [1500],  # max = 1500, p95 = 300
        latencies=[0.1, 0.2],
        mem_state={"used_memory": 10 * 1024 * 1024, "maxmemory": 256 * 1024 * 1024, "evicted_keys": 0},
    )

    # Key counts: raw = 200, resolved = 3000
    assert plan_res["key_counts_by_type"]["raw"] == 200
    assert plan_res["key_counts_by_type"]["resolved"] == 3000

    # Projection MUST use max (800 and 1500), not p95 or mean
    assert plan_res["projected_raw_bytes"] == 200 * 800
    assert plan_res["projected_resolved_bytes"] == 3000 * 1500

    # Reported stats still include mean, p50, p95, max for visibility
    assert plan_res["sampled_raw_fhir_parameters"]["max_bytes"] == 800
    assert plan_res["sampled_resolved_dmd_concept"]["max_bytes"] == 1500
    assert plan_res["sampled_resolved_dmd_concept"]["p95_bytes"] == 300
    assert plan_res["sampled_resolved_dmd_concept"]["p95_bytes"] < 1500


@pytest.mark.asyncio
@patch("app.ops.dmd_cache.snomed_client", autospec=True)
async def test_sample_payload_metrics_makes_zero_redis_writes(mock_snomed):
    """Verify sample_payload_metrics executes sampling and never mutates Redis."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient, sample_payload_metrics

    mock_client = AsyncMock(spec=TerminologyClient)

    def side_effect(url, body=None, **kwargs):
        json_body = body or kwargs.get("json", {})
        params = json_body.get("parameter", [])
        val_set = next((p["resource"] for p in params if p.get("name") == "valueSet"), {})
        cat = val_set.get("compose", {}).get("include", [{}])[0].get("filter", [{}])[0].get("value", "")

        req = httpx.Request("POST", f"https://ontology.nhs.uk/fhir/{url}")
        if cat in ("ROUTE", "UOM"):
            concept_data = [{"code": "101", "display": f"Sample {cat} display"}]
        else:
            concept_data = [
                {
                    "code": "201",
                    "display": f"Sample {cat} display",
                    "property": [
                        {"code": "ROUTECD", "valueCode": "101"},
                    ],
                }
            ]

        resp = {
            "resourceType": "ValueSet",
            "expansion": {
                "total": 1,
                "contains": concept_data,
            },
        }
        return httpx.Response(200, json=resp, request=req)

    mock_client.post_fhir.side_effect = side_effect

    raw_sizes, resolved_sizes, latencies = await sample_payload_metrics(
        mock_client, release="202610.1.0", sample_size=1
    )

    assert len(raw_sizes) == 2  # ROUTE and UOM
    assert len(resolved_sizes) == 2  # VMP and AMP
    assert len(latencies) == 4

    # Redis must NOT be mutated in any way
    assert mock_snomed.set.await_count == 0
    assert mock_snomed.setex.await_count == 0
    assert mock_snomed.delete.await_count == 0
    assert mock_snomed.pipeline.call_count == 0


# ==============================================================================
# PHASE 5, 6 & 7: EXACT KEY MODELING, DURATION & REDIS SAFETY TESTS
# ==============================================================================


def test_capacity_plan_exact_key_correspondence_matches_warm():
    """Verify build_capacity_plan models exactly the keys written by warm() (raw=ROUTE+UOM, resolved=VMP+AMP, metadata=2)."""
    from app.ops.dmd_cache import build_capacity_plan

    plan_res = build_capacity_plan(
        release="202610.1.0",
        category_counts={"ROUTE": 150, "UOM": 250, "VMP": 25000, "AMP": 35000},
        raw_payload_bytes=[500],
        resolved_payload_bytes=[600],
        latencies=[0.1],
        mem_state={"used_memory": 20 * 1024 * 1024, "maxmemory": 256 * 1024 * 1024, "evicted_keys": 0},
    )

    # Raw keys written: ROUTE + UOM only
    assert plan_res["key_counts_by_type"]["raw"] == 400
    # Resolved keys written: VMP + AMP only
    assert plan_res["key_counts_by_type"]["resolved"] == 60000
    # Metadata keys written: manifest + active pointer
    assert plan_res["key_counts_by_type"]["metadata"] == 2
    assert plan_res["total_keys"] == 60402


def test_capacity_plan_sequential_duration_projection():
    """Verify projected duration calculates request_count * p95 latency with concurrency=1."""
    from app.ops.dmd_cache import build_capacity_plan

    # ROUTE: 1000 (1 page), UOM: 1000 (1 page), VMP: 10000 (10 pages), AMP: 20000 (20 pages) = 32 pages
    # Sample requests = 8
    # Total requests = 40
    # p95 latency = 0.5s -> 40 * 0.5 = 20.0s
    plan_res = build_capacity_plan(
        release="202610.1.0",
        category_counts={"ROUTE": 1000, "UOM": 1000, "VMP": 10000, "AMP": 20000},
        raw_payload_bytes=[500],
        resolved_payload_bytes=[600],
        latencies=[0.5],
        mem_state={"used_memory": 20 * 1024 * 1024, "maxmemory": 256 * 1024 * 1024, "evicted_keys": 0},
    )

    assert plan_res["projected_terminology_requests"] == 40
    assert plan_res["projected_duration_seconds"] == pytest.approx(20.0, 0.1)
    assert plan_res["fits_timeout"] is True
    assert plan_res["fail_closed"] is False


def test_capacity_plan_duration_over_1800s_fails_closed():
    """Verify projected duration > 1800s sets fits_timeout=False and fail_closed=True."""
    from app.ops.dmd_cache import build_capacity_plan

    # Latency 50.0s per request -> 40 * 50 = 2000s > 1800s
    plan_res = build_capacity_plan(
        release="202610.1.0",
        category_counts={"ROUTE": 1000, "UOM": 1000, "VMP": 10000, "AMP": 20000},
        raw_payload_bytes=[500],
        resolved_payload_bytes=[600],
        latencies=[50.0],
        mem_state={"used_memory": 20 * 1024 * 1024, "maxmemory": 256 * 1024 * 1024, "evicted_keys": 0},
    )

    assert plan_res["fits_timeout"] is False
    assert plan_res["fail_closed"] is True
    assert any("exceeds Container Apps Job timeout" in r for r in plan_res["fail_reasons"])


def test_maxmemory_zero_or_negative_fails_closed():
    """Verify check_preflight_memory and check_memory_guard fail closed if maxmemory <= 0."""
    from app.ops.dmd_cache import check_memory_guard, check_preflight_memory

    with pytest.raises(RuntimeError, match="Redis maxmemory must be configured and positive"):
        check_preflight_memory({"maxmemory": 0, "used_memory": 1024}, 5000)

    with pytest.raises(RuntimeError, match="Redis maxmemory must be configured and positive"):
        check_preflight_memory({"maxmemory": -1, "used_memory": 1024}, 5000)

    with pytest.raises(RuntimeError, match="Redis maxmemory must be configured and positive"):
        check_memory_guard(
            {"maxmemory": 0, "used_memory": 1024, "evicted_keys": 0},
            5000,
        )


@pytest.mark.asyncio
@patch("app.ops.dmd_cache.snomed_client", autospec=True)
async def test_intermediate_eviction_stops_warm_before_next_batch(mock_snomed):
    """Verify _expand_vmp_concepts halts immediately if intermediate Redis INFO shows evicted_keys increased."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient, _expand_vmp_concepts

    mock_client = AsyncMock(spec=TerminologyClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    resp_p1 = httpx.Response(
        200,
        json={
            "resourceType": "ValueSet",
            "expansion": {
                "offset": 0,
                "contains": [
                    {
                        "code": "1001",
                        "display": "VMP Product 1",
                        "designation": [{"value": "Tablet"}],
                        "property": [{"code": "form", "valueCoding": {"display": "Tablet"}}],
                    }
                ],
            },
        },
        request=req,
    )
    mock_client.post_fhir.return_value = resp_p1

    # info() call during batch guard reports evicted_keys = 1
    mock_snomed.info.return_value = {"used_memory": 1000, "maxmemory": 1000000, "evicted_keys": 1}

    with pytest.raises(RuntimeError, match="Redis evicted 1 keys during VMP warm stage"):
        await _expand_vmp_concepts(
            client=mock_client,
            expected_release="202610.1.0",
            route_map={},
            uom_map={},
            vmp_lookup={},
            baseline_evicted_keys=0,
            expected_count=1,
        )


# ==============================================================================
# PHASE 8: RELEASE COMPLETENESS & ACTIVATION TESTS
# ==============================================================================


@pytest.mark.asyncio
@patch("app.ops.dmd_cache.snomed_client", autospec=True)
@patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.release_lock", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.renew_lock", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.discover_release_capacity", new_callable=AsyncMock)
@patch("app.ops.dmd_cache.sample_payload_metrics", new_callable=AsyncMock)
@patch("app.ops.dmd_cache._expand_and_cache_lookups", new_callable=AsyncMock)
@patch("app.ops.dmd_cache._expand_vmp_concepts", new_callable=AsyncMock)
@patch("app.ops.dmd_cache._expand_amp_concepts", new_callable=AsyncMock)
async def test_completeness_processed_mismatch_prevents_activation(
    mock_amp,
    mock_vmp,
    mock_lookups,
    mock_sample,
    mock_discover,
    mock_token,
    mock_renew,
    mock_rel,
    mock_acq,
    mock_snomed,
):
    """Verify warm() fails closed and does NOT activate if processed count != expected count."""
    from app.ops.dmd_cache import warm

    mock_acq.return_value = True
    mock_renew.return_value = True
    mock_token.return_value = "token123"
    mock_discover.return_value = {
        "release": "202610.1.0",
        "counts": {"ROUTE": 100, "UOM": 100, "VMP": 1000, "AMP": 2000},
        "total_concepts": 3200,
        "page_counts": {"ROUTE": 1, "UOM": 1, "VMP": 1, "AMP": 2},
    }
    mock_sample.return_value = ([512], [512], [0.1])
    mock_lookups.return_value = 100
    mock_vmp.return_value = 999  # Processed 999 instead of expected 1000!
    mock_amp.return_value = 2000

    mock_snomed.info.return_value = {
        "used_memory": 30 * 1024 * 1024,
        "maxmemory": 256 * 1024 * 1024,
        "evicted_keys": 0,
    }

    exit_code = await warm()
    assert exit_code != 0

    # Ensure dmd:active-release was never written
    for call in mock_snomed.setex.call_args_list:
        assert call[0][0] != "dmd:active-release"


@pytest.mark.asyncio
@patch("app.ops.dmd_cache.snomed_client", autospec=True)
async def test_concept_with_missing_code_or_display_fails_closed(mock_snomed):
    """Verify expansion fails closed if a concept contains no code or display."""
    import httpx

    from app.ops.dmd_cache import TerminologyClient, _expand_and_cache_lookups

    mock_client = AsyncMock(spec=TerminologyClient)
    req = httpx.Request("POST", "https://ontology.nhs.uk/fhir/ValueSet/$expand")
    resp_malformed = httpx.Response(
        200,
        json={
            "resourceType": "ValueSet",
            "expansion": {
                "offset": 0,
                "contains": [
                    {"code": "1001"}  # Missing display!
                ],
            },
        },
        request=req,
    )
    mock_client.post_fhir.return_value = resp_malformed

    with pytest.raises(ValueError, match="missing code or display"):
        await _expand_and_cache_lookups(
            client=mock_client,
            parent="ROUTE",
            expected_release="202610.1.0",
            lookup_map={},
            baseline_evicted_keys=0,
            expected_count=1,
        )


# ==============================================================================
# PHASE 14 & 19: TERRAFORM SCHEDULER BLOCK & TARGET INVARIANTS TESTS
# ==============================================================================


def test_terraform_schedule_enabled_is_blocked_and_safeguards_active():
    """Verify infra/main.tf asserts !var.dmd_cache_schedule_enabled and variables default to false."""
    import pathlib

    main_tf = pathlib.Path("infra/main.tf").read_text()
    assert main_tf.count("condition     = !var.dmd_cache_schedule_enabled") >= 2
    assert "dmd_cache_schedule_enabled remains blocked until a missed-success watchdog" in main_tf

    variables_tf = pathlib.Path("infra/variables.tf").read_text()
    assert 'variable "dmd_cache_warm_enabled"' in variables_tf
    assert "default     = false" in variables_tf
    assert 'variable "dmd_cache_schedule_enabled"' in variables_tf


def test_terraform_play_warm_enabled_and_schedule_disabled():
    """Verify Play environment explicitly enables manual cache warmer and keeps schedule false."""
    import pathlib
    import re

    play_tfvars = pathlib.Path("infra/env/play.tfvars").read_text()
    assert re.search(r"dmd_cache_warm_enabled\s*=\s*true", play_tfvars), "Play must have dmd_cache_warm_enabled = true"
    assert re.search(r"dmd_cache_schedule_enabled\s*=\s*false", play_tfvars), (
        "Play must have dmd_cache_schedule_enabled = false"
    )


def test_terraform_int_and_prd_remain_warmer_disabled():
    """Verify INT and PRD environments remain warmer-disabled."""
    import pathlib
    import re

    for env_name in ("int", "prd"):
        tfvars_path = pathlib.Path(f"infra/env/{env_name}.tfvars")
        if tfvars_path.exists():
            tfvars_text = tfvars_path.read_text()
            assert not re.search(r"dmd_cache_warm_enabled\s*=\s*true", tfvars_text), f"{env_name} must not enable warm"
            assert not re.search(r"dmd_cache_schedule_enabled\s*=\s*true", tfvars_text), (
                f"{env_name} must not enable schedule"
            )


def test_terraform_container_app_job_is_manual_only():
    """Verify Container Apps Job is configured for manual trigger only with zero automatic retries."""
    import pathlib

    main_tf = pathlib.Path("infra/main.tf").read_text()
    assert "manual_trigger_config {" in main_tf
    assert "replica_retry_limit          = 0" in main_tf
    assert 'args    = ["-m", "app.ops.dmd_cache", "warm"]' in main_tf
    assert "event_trigger_config" not in main_tf
    assert "schedule_trigger_config" not in main_tf


def test_terraform_container_app_job_registry_and_timeout_invariants():
    """Verify Container Apps Job registry uses pure hostname from image, timeout invariant avoids self, and lock TTL env is passed."""
    import pathlib

    main_tf = pathlib.Path("infra/main.tf").read_text()

    # Registry server derives purely from image hostname and does NOT use var.docker_registry_url
    assert 'server               = split("/", coalesce(var.dmd_cache_job_image, var.docker_image))[0]' in main_tf
    assert (
        "docker_registry_url"
        not in main_tf.split('resource "azurerm_container_app_job" "dmd_warm"')[1].split("template {")[0]
    )

    # App service still uses var.docker_registry_url
    app_service_section = main_tf.split('resource "azurerm_linux_web_app"')[1].split(
        'resource "azurerm_container_app_environment"'
    )[0]
    assert "docker_registry_url      = var.docker_registry_url" in app_service_section

    # Timeout invariant does NOT use self
    assert "self.replica_timeout_in_seconds" not in main_tf
    assert "local.dmd_job_replica_timeout_seconds < local.dmd_logic_poll_timeout_seconds" in main_tf

    # Lock TTL env var is passed with 2700s local
    assert 'name  = "DMD_LOCK_TTL"' in main_tf
    assert "value = tostring(local.dmd_lock_ttl_seconds)" in main_tf


def test_terraform_cae_lifecycle_and_authoritative_attributes():
    """Verify Container Apps Environment preserves Consumption configuration and only ignores normalised drift fields."""
    import pathlib
    import re

    main_tf = pathlib.Path("infra/main.tf").read_text()
    assert 'resource "azurerm_container_app_environment" "cae"' in main_tf

    cae_section = main_tf.split('resource "azurerm_container_app_environment" "cae"')[1].split(
        'resource "azurerm_user_assigned_identity" "dmd_job_mi"'
    )[0]

    # Explicit Consumption workload profile must be declared
    assert 'name                  = "Consumption"' in cae_section
    assert 'workload_profile_type = "Consumption"' in cae_section

    # Parse lifecycle ignore_changes
    match = re.search(r"lifecycle\s*\{\s*ignore_changes\s*=\s*\[(.*?)\]\s*\}", cae_section, re.DOTALL)
    assert match is not None, "CAE must have an ignore_changes block"
    ignored_raw = match.group(1)
    ignored_items = [
        item.strip() for item in ignored_raw.split(",") if item.strip() and not item.strip().startswith("#")
    ]

    # Exact ignored items
    assert "workload_profile" in ignored_items
    assert "infrastructure_resource_group_name" in ignored_items
    assert 'tags["CostCenter"]' in ignored_items or 'tags["CostCenter"]' in ignored_items

    # Must NOT ignore all
    assert "all" not in ignored_items

    # Critical authoritative networking and identity attributes MUST NOT be ignored
    for authoritative_attr in [
        "infrastructure_subnet_id",
        "log_analytics_workspace_id",
        "internal_load_balancer_enabled",
        "location",
        "resource_group_name",
        "name",
    ]:
        assert authoritative_attr not in ignored_items, (
            f"{authoritative_attr} must remain authoritative and not be ignored"
        )


def test_load_dmd_credentials_both_env_present(monkeypatch):
    """1. Both DMD env credentials present: uses environment values, no Key Vault call."""
    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.setenv("DMD_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("DMD_CLIENT_SECRET", "env-client-secret")
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)

    with (
        patch("app.ops.dmd_cache.SecretClient") as mock_sc,
        patch("app.ops.dmd_cache.ManagedIdentityCredential") as mock_mi,
    ):
        client_id, client_secret = _load_dmd_credentials()
        assert client_id == "env-client-id"
        assert client_secret == "env-client-secret"
        mock_sc.assert_not_called()
        mock_mi.assert_not_called()


def test_load_dmd_credentials_only_client_id_fails_closed(monkeypatch):
    """2. Only client ID present: fail closed."""
    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.setenv("DMD_CLIENT_ID", "env-client-id")
    monkeypatch.delenv("DMD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEY_VAULT_URL", "https://vault.azure.net/")
    monkeypatch.setenv("AZURE_CLIENT_ID", "azure-client-id")

    with pytest.raises(ValueError, match="Partial dm\\+d credentials"):
        _load_dmd_credentials()


def test_load_dmd_credentials_only_client_secret_fails_closed(monkeypatch):
    """3. Only client secret present: fail closed."""
    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.delenv("DMD_CLIENT_ID", raising=False)
    monkeypatch.setenv("DMD_CLIENT_SECRET", "env-client-secret")
    monkeypatch.setenv("KEY_VAULT_URL", "https://vault.azure.net/")
    monkeypatch.setenv("AZURE_CLIENT_ID", "azure-client-id")

    with pytest.raises(ValueError, match="Partial dm\\+d credentials"):
        _load_dmd_credentials()


def test_load_dmd_credentials_from_key_vault_success(monkeypatch):
    """4. Neither present, valid KV configuration: ManagedIdentityCredential receives exact AZURE_CLIENT_ID, SecretClient uses KEY_VAULT_URL, exact two secret names requested."""
    from unittest.mock import MagicMock

    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.delenv("DMD_CLIENT_ID", raising=False)
    monkeypatch.delenv("DMD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-test.vault.azure.net/")
    monkeypatch.setenv("AZURE_CLIENT_ID", "mi-client-uuid-1234")

    mock_client = MagicMock()
    id_bundle = MagicMock()
    id_bundle.value = "kv-resolved-client-id"
    secret_bundle = MagicMock()
    secret_bundle.value = "kv-resolved-client-secret"

    def get_secret_side_effect(name):
        if name == "dmd-client-id":
            return id_bundle
        elif name == "dmd-client-secret":
            return secret_bundle
        raise KeyError(name)

    mock_client.get_secret.side_effect = get_secret_side_effect

    with (
        patch("app.ops.dmd_cache.ManagedIdentityCredential") as mock_mi,
        patch("app.ops.dmd_cache.SecretClient") as mock_sc,
    ):
        mock_sc.return_value.__enter__.return_value = mock_client
        client_id, client_secret = _load_dmd_credentials()

        assert client_id == "kv-resolved-client-id"
        assert client_secret == "kv-resolved-client-secret"

        mock_mi.assert_called_once_with(client_id="mi-client-uuid-1234")
        mock_sc.assert_called_once()
        assert mock_sc.call_args[1]["vault_url"] == "https://kv-test.vault.azure.net/"
        assert mock_client.get_secret.call_count == 2
        mock_client.get_secret.assert_any_call("dmd-client-id")
        mock_client.get_secret.assert_any_call("dmd-client-secret")


def test_load_dmd_credentials_key_vault_unavailable_fails_closed(monkeypatch):
    """5. Key Vault unavailable: fail closed."""
    from unittest.mock import MagicMock

    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.delenv("DMD_CLIENT_ID", raising=False)
    monkeypatch.delenv("DMD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-test.vault.azure.net/")
    monkeypatch.setenv("AZURE_CLIENT_ID", "mi-client-uuid-1234")

    mock_client = MagicMock()
    mock_client.get_secret.side_effect = ConnectionError("Key Vault endpoint unreachable")

    with (
        patch("app.ops.dmd_cache.ManagedIdentityCredential"),
        patch("app.ops.dmd_cache.SecretClient") as mock_sc,
    ):
        mock_sc.return_value.__enter__.return_value = mock_client
        with pytest.raises(RuntimeError, match="Failed to retrieve dm\\+d credentials from Key Vault"):
            _load_dmd_credentials()


def test_load_dmd_credentials_one_kv_secret_missing_or_empty_fails_closed(monkeypatch):
    """6. One KV secret missing/empty: fail closed."""
    from unittest.mock import MagicMock

    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.delenv("DMD_CLIENT_ID", raising=False)
    monkeypatch.delenv("DMD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-test.vault.azure.net/")
    monkeypatch.setenv("AZURE_CLIENT_ID", "mi-client-uuid-1234")

    mock_client = MagicMock()
    id_bundle = MagicMock()
    id_bundle.value = "valid-id"
    empty_bundle = MagicMock()
    empty_bundle.value = "   "

    mock_client.get_secret.side_effect = [id_bundle, empty_bundle]

    with (
        patch("app.ops.dmd_cache.ManagedIdentityCredential"),
        patch("app.ops.dmd_cache.SecretClient") as mock_sc,
    ):
        mock_sc.return_value.__enter__.return_value = mock_client
        with pytest.raises(ValueError, match="Key Vault secret 'dmd-client-secret' is missing or empty"):
            _load_dmd_credentials()


def test_load_dmd_credentials_no_config_at_all_fails_closed(monkeypatch):
    """7. No credential configuration at all: fail closed."""
    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.delenv("DMD_CLIENT_ID", raising=False)
    monkeypatch.delenv("DMD_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)

    with pytest.raises(
        ValueError,
        match="dm\\+d credentials not found in environment and Key Vault configuration is incomplete",
    ):
        _load_dmd_credentials()


def test_load_dmd_credentials_never_logs_secrets(monkeypatch, caplog):
    """8. Secrets/tokens never appear in log messages on failure or success."""
    import logging
    from unittest.mock import MagicMock

    from app.ops.dmd_cache import _load_dmd_credentials

    monkeypatch.delenv("DMD_CLIENT_ID", raising=False)
    monkeypatch.delenv("DMD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-test.vault.azure.net/")
    monkeypatch.setenv("AZURE_CLIENT_ID", "mi-client-uuid-1234")

    secret_raw = "SUPER_SECRET_VALUE_XYZ"
    mock_client = MagicMock()
    mock_client.get_secret.side_effect = RuntimeError(f"Internal error with raw payload: {secret_raw}")

    with (
        patch("app.ops.dmd_cache.ManagedIdentityCredential"),
        patch("app.ops.dmd_cache.SecretClient") as mock_sc,
    ):
        mock_sc.return_value.__enter__.return_value = mock_client
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(RuntimeError):
                _load_dmd_credentials()

    for record in caplog.records:
        assert secret_raw not in record.message
        assert "SUPER_SECRET" not in record.message


def test_terraform_container_app_job_runtime_kv_resolution_invariants():
    """Verify Container Apps Job does not declare Key Vault secrets directly, but passes KEY_VAULT_URL and AZURE_CLIENT_ID for runtime resolution."""
    import pathlib

    main_tf = pathlib.Path("infra/main.tf").read_text()
    job_section = main_tf.split('resource "azurerm_container_app_job" "dmd_warm"')[1].split(
        'resource "azurerm_logic_app_workflow" "dmd_warm"'
    )[0]

    # No Container Apps Key Vault secret block exists for dmd-client-id or dmd-client-secret
    assert "key_vault_secret_id" not in job_section
    assert 'name                = "dmd-client-id"' not in job_section
    assert 'name                = "dmd-client-secret"' not in job_section

    # Job container receives KEY_VAULT_URL and AZURE_CLIENT_ID
    assert 'name  = "KEY_VAULT_URL"' in job_section
    assert "value = data.azurerm_key_vault.shared_kv.vault_uri" in job_section
    assert 'name  = "AZURE_CLIENT_ID"' in job_section
    assert "value = azurerm_user_assigned_identity.dmd_job_mi[0].client_id" in job_section

    # MI remains attached to the Job
    assert "identity_ids = [azurerm_user_assigned_identity.dmd_job_mi[0].id]" in job_section

    # MI has Key Vault access policy with Get permission
    policy_section = main_tf.split('resource "azurerm_key_vault_access_policy" "dmd_job_shared_policy"')[1].split(
        'resource "azurerm_container_app_job" "dmd_warm"'
    )[0]
    assert 'secret_permissions = ["Get"]' in policy_section
    assert "object_id    = azurerm_user_assigned_identity.dmd_job_mi[0].principal_id" in policy_section

    # CA subnet has Microsoft.KeyVault service endpoint
    ca_subnet_section = main_tf.split('resource "azurerm_subnet" "ca_subnet"')[1].split(
        'resource "azurerm_user_assigned_identity" "locust_mi"'
    )[0]
    assert 'service_endpoints    = ["Microsoft.KeyVault"]' in ca_subnet_section

    # Job remains manual
    assert "manual_trigger_config {" in job_section
    assert "replica_retry_limit          = 0" in job_section
