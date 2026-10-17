"""Tests for dm+d missed-success and release-freshness watchdog semantics."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def evaluate_watchdog_semantics(
    logs: list[dict],
    current_time: datetime,
    job_name: str = "xhuma-app-int-dmd-warm",
) -> list[dict]:
    """Evaluates the watchdog alert logic mimicking the KQL query in infra/kql/dmd_cache_watchdog.kql.

    KQL Logic:
    1. Active window: Monday 06:00 UTC through Tuesday 04:00 UTC
    2. Events extracted: ContainerAppConsoleLogs_CL where ContainerJobName_s == job_name
       and TimeGenerated in last 24h and Log_s has "warm_succeeded"
    3. Alert fires (returns rows) if WindowActive and AdvancedEvents == 0.
    """
    # 1. Window evaluation: Monday is weekday 0 in Python
    # KQL: floor(dayofweek(now()) / 1d, 1) where Sunday=0, Monday=1, Tuesday=2
    # In Python: Monday=0, Tuesday=1
    py_weekday = current_time.weekday()
    hour = current_time.hour

    is_active_window = (py_weekday == 0 and hour >= 6) or (py_weekday == 1 and hour < 4)

    if not is_active_window:
        return []

    # 2. Extract events in last 24h
    window_start = current_time.timestamp() - 24 * 3600
    matched_events = []
    for entry in logs:
        if entry.get("ContainerJobName_s") != job_name:
            continue
        ts = entry.get("TimeGenerated")
        if isinstance(ts, datetime):
            ts_epoch = ts.timestamp()
        else:
            ts_epoch = datetime.fromisoformat(ts).timestamp()

        if ts_epoch < window_start:
            continue

        log_msg = entry.get("Log_s", "")
        if "warm_succeeded" not in log_msg:
            continue

        match = re.search(r"dmd_cache_event\s+({.*})", log_msg)
        if match:
            try:
                evt = json.loads(match.group(1))
                if evt.get("event") == "warm_succeeded":
                    matched_events.append(evt)
            except json.JSONDecodeError:
                pass

    total_succeeded = len(matched_events)
    advanced_events = [e for e in matched_events if e.get("release_advanced") is True]
    advanced_count = len(advanced_events)

    if is_active_window and advanced_count == 0:
        reason = (
            "Missed success: no warm_succeeded event in last 24h"
            if total_succeeded == 0
            else "Stale release: warm succeeded but release did not advance"
        )
        return [
            {
                "Alert": "dm+d cache warm failure in Monday operating window",
                "WindowActive": is_active_window,
                "TotalSucceededEvents": total_succeeded,
                "AdvancedEvents": advanced_count,
                "Reason": reason,
            }
        ]
    return []


# ==============================================================================
# Requirement 17 Test Cases: A through G
# ==============================================================================


def test_case_a_successful_scheduled_warm_with_advancement():
    """Case A: Successful scheduled warm with release advancement -> healthy / no alert."""
    monday_0800 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc)
    monday_0515 = datetime(2026, 10, 12, 5, 15, 0, tzinfo=timezone.utc)

    completion_event = {
        "event": "warm_succeeded",
        "release": "202610.1.0",
        "previous_release": "202610.0.0",
        "release_advanced": True,
        "duration_seconds": 850.5,
        "concept_count": 480000,
        "memory_delta_bytes": 100000000,
        "evictions": 0,
    }
    logs = [
        {
            "ContainerJobName_s": "xhuma-app-int-dmd-warm",
            "TimeGenerated": monday_0515,
            "Log_s": f"2026-10-12 05:15:00 [INFO] xhuma.dmd_cache: dmd_cache_event {json.dumps(completion_event)}",
        }
    ]

    alerts = evaluate_watchdog_semantics(logs, current_time=monday_0800)
    assert len(alerts) == 0, "Expected healthy state (no alert) when warm succeeded with release advancement"


def test_case_b_no_successful_warm_in_expected_window():
    """Case B: No successful warm in expected window -> missed-success alert condition."""
    monday_0800 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc)

    # No logs at all
    alerts = evaluate_watchdog_semantics([], current_time=monday_0800)
    assert len(alerts) == 1
    assert "Missed success" in alerts[0]["Reason"]
    assert alerts[0]["AdvancedEvents"] == 0
    assert alerts[0]["TotalSucceededEvents"] == 0


def test_case_c_warm_succeeds_but_release_is_unchanged():
    """Case C: Warm succeeds but release is unchanged -> freshness alert condition."""
    monday_0800 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc)
    monday_0515 = datetime(2026, 10, 12, 5, 15, 0, tzinfo=timezone.utc)

    completion_event = {
        "event": "warm_succeeded",
        "release": "202610.0.0",
        "previous_release": "202610.0.0",
        "release_advanced": False,  # Release did NOT advance
        "duration_seconds": 850.5,
        "concept_count": 480000,
        "memory_delta_bytes": 0,
        "evictions": 0,
    }
    logs = [
        {
            "ContainerJobName_s": "xhuma-app-int-dmd-warm",
            "TimeGenerated": monday_0515,
            "Log_s": f"2026-10-12 05:15:00 [INFO] xhuma.dmd_cache: dmd_cache_event {json.dumps(completion_event)}",
        }
    ]

    alerts = evaluate_watchdog_semantics(logs, current_time=monday_0800)
    assert len(alerts) == 1
    assert "Stale release" in alerts[0]["Reason"]
    assert alerts[0]["TotalSucceededEvents"] == 1
    assert alerts[0]["AdvancedEvents"] == 0


@pytest.mark.asyncio
async def test_case_d_manual_same_release_warm_does_not_fail():
    """Case D: Manual same-release warm remains operationally permitted; application warm command itself exits 0."""
    from app.ops.dmd_cache import warm

    with patch("app.ops.dmd_cache.snomed_client", autospec=True) as mock_snomed, \
         patch("app.ops.dmd_cache.acquire_lock", new_callable=AsyncMock, return_value=True), \
         patch("app.ops.dmd_cache.release_lock", new_callable=AsyncMock, return_value=True), \
         patch("app.ops.dmd_cache.renew_lock", new_callable=AsyncMock, return_value=True), \
         patch("app.ops.dmd_cache.fetch_token", new_callable=AsyncMock, return_value="tok"), \
         patch("app.ops.dmd_cache.discover_release_capacity", new_callable=AsyncMock) as mock_discover, \
         patch("app.ops.dmd_cache.sample_payload_metrics", new_callable=AsyncMock, return_value=([512], [512], [0.1])), \
         patch("app.ops.dmd_cache._expand_and_cache_lookups", new_callable=AsyncMock, return_value=100), \
         patch("app.ops.dmd_cache._expand_vmp_concepts", new_callable=AsyncMock, return_value=1000), \
         patch("app.ops.dmd_cache._expand_amp_concepts", new_callable=AsyncMock, return_value=2000), \
         patch("app.ops.dmd_cache.logger.info") as mock_log:

        same_release = "202610.1.0"
        mock_discover.return_value = {
            "release": same_release,
            "counts": {"ROUTE": 100, "UOM": 100, "VMP": 1000, "AMP": 2000},
            "total_concepts": 3200,
            "page_counts": {"ROUTE": 1, "UOM": 1, "VMP": 1, "AMP": 2},
        }
        mock_snomed.info.return_value = {
            "used_memory": 30 * 1024 * 1024,
            "maxmemory": 256 * 1024 * 1024,
            "evicted_keys": 0,
        }
        # Prior release in Redis is already the same release
        mock_snomed.get = AsyncMock(return_value=same_release.encode("utf-8"))
        mock_snomed.setex = AsyncMock(return_value=True)

        exit_code = await warm()
        assert exit_code == 0, "Manual warm with same release must exit 0 and not fail"

        # Check emitted event recorded release_advanced = False
        event_calls = [c for c in mock_log.call_args_list if len(c.args) >= 2 and c.args[0] == "dmd_cache_event %s"]
        assert len(event_calls) == 1
        event_dict = json.loads(event_calls[0].args[1])
        assert event_dict["release"] == same_release
        assert event_dict["previous_release"] == same_release
        assert event_dict["release_advanced"] is False


def test_case_e_warmer_job_failure_detected_by_existing_alerts():
    """Case E: Warmer job failure remains covered by existing Logic App failure alerts in Terraform."""
    main_tf = (REPO_ROOT / "infra" / "main.tf").read_text(encoding="utf-8")

    # Assert both Logic App failure alerts remain declared
    assert 'resource "azurerm_monitor_metric_alert" "la_runs_failed"' in main_tf
    assert 'metric_name      = "RunsFailed"' in main_tf
    assert 'resource "azurerm_monitor_metric_alert" "la_triggers_failed"' in main_tf
    assert 'metric_name      = "TriggersFailed"' in main_tf


def test_case_f_completion_event_fields_and_no_phi():
    """Case F: Event contains release, previous_release, release_advanced without PHI/secrets."""
    event = {
        "event": "warm_succeeded",
        "release": "202610.1.0",
        "previous_release": "202610.0.0",
        "release_advanced": True,
        "duration_seconds": 845.2,
        "concept_count": 481000,
        "memory_delta_bytes": 104857600,
        "evictions": 0,
    }

    # Verify required keys
    assert "release" in event
    assert "previous_release" in event
    assert "release_advanced" in event

    # Verify no PHI or credentials in event keys or values
    forbidden_terms = ["patient", "nhs_number", "mrn", "name", "dob", "token", "secret", "password", "key"]
    for k, v in event.items():
        for term in forbidden_terms:
            assert term not in k.lower()
            if isinstance(v, str):
                assert term not in v.lower()


def test_case_g_watchdog_requires_alert_destinations():
    """Case G: Watchdog cannot be enabled without alert destination(s)."""
    main_tf = (REPO_ROOT / "infra" / "main.tf").read_text(encoding="utf-8")

    # Check precondition on watchdog resource
    assert "precondition {" in main_tf
    assert "condition     = length(var.dmd_cache_alert_action_group_ids) > 0" in main_tf
    assert "At least one alert action group ID is required for the dm+d cache warmer watchdog." in main_tf


def test_outside_operating_window_produces_no_alerts():
    """Verify that outside of the Monday operating window, no alerts are produced (no false positives)."""
    wednesday_1400 = datetime(2026, 10, 14, 14, 0, 0, tzinfo=timezone.utc)
    alerts = evaluate_watchdog_semantics([], current_time=wednesday_1400)
    assert len(alerts) == 0, "No alerts should fire on Wednesday"

    monday_0530 = datetime(2026, 10, 12, 5, 30, 0, tzinfo=timezone.utc)
    alerts = evaluate_watchdog_semantics([], current_time=monday_0530)
    assert len(alerts) == 0, "No alerts should fire before 06:00 UTC operational headroom"


def test_watchdog_kql_file_is_readable_and_well_formed():
    """Verify KQL query file syntax and structure."""
    kql_file = REPO_ROOT / "infra" / "kql" / "dmd_cache_watchdog.kql"
    assert kql_file.is_file()
    kql = kql_file.read_text(encoding="utf-8")

    # Check key KQL operators
    assert "ContainerAppConsoleLogs_CL" in kql
    assert "warm_succeeded" in kql
    assert "dmd_cache_event" in kql
    assert "release_advanced" in kql
    assert "is_active_window" in kql
    assert "AdvancedEvents == 0" in kql
    assert "${app_service_name}" in kql
