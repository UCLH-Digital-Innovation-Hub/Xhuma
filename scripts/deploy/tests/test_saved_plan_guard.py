"""Check migration diagnostics and saved-plan image checks without Terraform."""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
plans = importlib.import_module("saved_plan")
IMAGE = "ghcr.io/org/repo@sha256:" + "a" * 64


@pytest.fixture
def plan(monkeypatch, tmp_path):
    monkeypatch.setenv("TF_VAR_docker_image", IMAGE)
    monkeypatch.delenv("TF_VAR_dmd_cache_job_image", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    return {"variables": {"docker_image": {"value": IMAGE}}, "resource_changes": []}


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["replace"]])
@pytest.mark.parametrize("replace_paths", [None, [], [["workload_profile"], ["template", 0, "container", 0, "image"]]])
def test_adopted_target_rejects_destructive_changes_with_safe_diagnostics(plan, actions, replace_paths, tmp_path):
    plan["resource_changes"] = [
        {
            "address": "azurerm_container_app_environment.cae[0]",
            "change": {
                "actions": actions,
                "replace_paths": replace_paths,
                "before": {"secret_token": "SENSITIVE_OLD_VALUE"},
                "after": {"password": "SENSITIVE_NEW_VALUE"},
            },
        },
        {"address": "azurerm_subnet.unchanged", "change": {"actions": ["no-op"]}},
    ]

    with pytest.raises(ValueError, match="Migration guard") as error:
        plans.guard_plan(plan, {"require_existing_state": True})

    for diagnostic in (str(error.value), (tmp_path / "summary").read_text()):
        assert "azurerm_container_app_environment.cae[0]" in diagnostic
        assert "actions: " in diagnostic
        assert "replace_paths:\n" in diagnostic
        if replace_paths:
            assert '  - ["workload_profile"]' in diagnostic
            assert '  - ["template",0,"container",0,"image"]' in diagnostic
        else:
            assert "  - []" in diagnostic
        assert "azurerm_subnet.unchanged" not in diagnostic
        assert "SENSITIVE_OLD_VALUE" not in diagnostic
        assert "SENSITIVE_NEW_VALUE" not in diagnostic
        assert "secret_token" not in diagnostic
        assert "password" not in diagnostic


def test_new_target_warns_about_destructive_actions_without_exposing_values(plan, capsys, tmp_path):
    plan["resource_changes"] = [
        {
            "address": "azurerm_container_app_job.dmd_warm[0]",
            "change": {"actions": ["replace"], "before": {"token": "SECRET"}, "after": {"token": "NEW_SECRET"}},
        }
    ]

    assert plans.guard_plan(plan, {}) == ["replace azurerm_container_app_job.dmd_warm[0]"]

    for diagnostic in (capsys.readouterr().out, (tmp_path / "summary").read_text()):
        assert "azurerm_container_app_job.dmd_warm[0]" in diagnostic
        assert 'actions: ["replace"]' in diagnostic
        assert "replace_paths:\n  - []" in diagnostic
        assert "SECRET" not in diagnostic
        assert "token" not in diagnostic


def test_adopted_target_allows_non_destructive_changes(plan, tmp_path):
    plan["resource_changes"] = [
        {"address": "app.updated", "change": {"actions": ["update"]}},
        {"address": "app.created", "change": {"actions": ["create"]}},
        {"address": "app.unchanged", "change": {"actions": ["no-op"]}},
        {"address": "data.read", "change": {"actions": ["read"]}},
    ]

    assert plans.guard_plan(plan, {"require_existing_state": True}) == ["update app.updated", "create app.created"]
    assert not (tmp_path / "summary").exists()


@pytest.mark.parametrize("image", [None, "ghcr.io/org/repo@sha256:" + "b" * 64])
def test_saved_plan_rejects_missing_or_wrong_application_image(plan, image):
    plan["variables"]["docker_image"]["value"] = image

    with pytest.raises(ValueError, match="different image"):
        plans.guard_plan(plan, {})


@pytest.mark.parametrize("image", [None, "ghcr.io/org/repo@sha256:" + "b" * 64])
def test_saved_plan_rejects_missing_or_wrong_cache_job_image_when_requested(plan, monkeypatch, image):
    monkeypatch.setenv("TF_VAR_dmd_cache_job_image", IMAGE)
    plan["variables"]["dmd_cache_job_image"] = {"value": image}

    with pytest.raises(ValueError, match="different dm\\+d cache job image"):
        plans.guard_plan(plan, {})


def test_saved_plan_accepts_matching_cache_job_image(plan, monkeypatch):
    monkeypatch.setenv("TF_VAR_dmd_cache_job_image", IMAGE)
    plan["variables"]["dmd_cache_job_image"] = {"value": IMAGE}

    assert plans.guard_plan(plan, {}) == []


def test_saved_plan_does_not_require_cache_job_override(plan):
    assert plans.guard_plan(plan, {}) == []
