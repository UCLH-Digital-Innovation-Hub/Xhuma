import json
from pathlib import Path

import yaml

from scripts.deploy.validate_action_groups import (
    is_scheduling_enabled_in_tfvars,
    validate_action_groups,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_matrix_deploy_workflow():
    path = REPO_ROOT / ".github" / "workflows" / "matrix-deploy.yml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_workflow_has_action_group_validation_step():
    wf = load_matrix_deploy_workflow()
    plan_job = wf["jobs"]["infra-plan"]
    steps = {step.get("name"): step for step in plan_job["steps"]}

    assert "Validate dm+d Action Group Configuration" in steps
    val_step = steps["Validate dm+d Action Group Configuration"]
    assert val_step.get("env", {}).get("DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON") == (
        "${{ secrets.DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON }}"
    )
    assert "validate_action_groups.py" in val_step.get("run", "")


def test_workflow_passes_tf_var_to_plan():
    wf = load_matrix_deploy_workflow()
    plan_job = wf["jobs"]["infra-plan"]
    plan_step = None
    for step in plan_job["steps"]:
        if step.get("name") == "Terraform Plan":
            plan_step = step
            break

    assert plan_step is not None
    plan_env = plan_step.get("env", {})
    assert plan_env.get("TF_VAR_dmd_cache_alert_action_group_ids") == (
        "${{ env.TF_VAR_dmd_cache_alert_action_group_ids }}"
    )


def test_scheduling_detection_in_tfvars():
    play_tfvars = REPO_ROOT / "infra" / "env" / "play.tfvars"
    int_tfvars = REPO_ROOT / "infra" / "env" / "int.tfvars"
    prd_tfvars = REPO_ROOT / "infra" / "env" / "prd.tfvars"

    assert is_scheduling_enabled_in_tfvars(play_tfvars) is False
    assert is_scheduling_enabled_in_tfvars(int_tfvars) is True
    assert is_scheduling_enabled_in_tfvars(prd_tfvars) is True


def test_play_scheduling_disabled_does_not_require_secret(tmp_path, monkeypatch, capsys):
    tfvars = tmp_path / "test_play.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = false\n")

    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))

    # Without secret
    exit_code = validate_action_groups(tfvars, raw_secret=None)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "dm+d cache scheduling is disabled" in captured.out
    assert "::error::" not in captured.out

    # Verify GITHUB_ENV exported empty list
    content = env_file.read_text()
    assert "TF_VAR_dmd_cache_alert_action_group_ids=[]" in content


def test_int_scheduling_enabled_missing_secret_fails_closed(tmp_path, capsys):
    tfvars = tmp_path / "test_int.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = true\n")

    exit_code = validate_action_groups(tfvars, raw_secret=None)
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "::error::" in captured.out
    assert "DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON secret is absent or empty" in captured.out


def test_int_scheduling_enabled_empty_string_fails_closed(tmp_path, capsys):
    tfvars = tmp_path / "test_int.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = true\n")

    exit_code = validate_action_groups(tfvars, raw_secret="   ")
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "::error::" in captured.out


def test_int_scheduling_enabled_malformed_json_fails_closed(tmp_path, capsys):
    tfvars = tmp_path / "test_int.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = true\n")

    exit_code = validate_action_groups(tfvars, raw_secret="[invalid json")
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "::error::" in captured.out
    assert "malformed JSON" in captured.out


def test_int_scheduling_enabled_non_list_fails_closed(tmp_path, capsys):
    tfvars = tmp_path / "test_int.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = true\n")

    exit_code = validate_action_groups(tfvars, raw_secret='{"id": "ag1"}')
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "::error::" in captured.out
    assert "must be a JSON array of strings" in captured.out


def test_int_scheduling_enabled_empty_list_fails_closed(tmp_path, capsys):
    tfvars = tmp_path / "test_int.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = true\n")

    exit_code = validate_action_groups(tfvars, raw_secret="[]")
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "::error::" in captured.out
    assert "resolves to an empty list" in captured.out


def test_int_scheduling_enabled_invalid_elements_fails_closed(tmp_path, capsys):
    tfvars = tmp_path / "test_int.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = true\n")

    exit_code = validate_action_groups(tfvars, raw_secret='["valid-id", 12345]')
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "::error::" in captured.out
    assert "invalid or empty resource IDs" in captured.out


def test_int_scheduling_enabled_valid_secret_succeeds(tmp_path, monkeypatch, capsys):
    tfvars = tmp_path / "test_int.tfvars"
    tfvars.write_text("dmd_cache_schedule_enabled = true\n")

    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))

    secret_val = '["/subscriptions/s1/resourceGroups/r1/providers/Microsoft.Insights/actionGroups/ag1"]'
    exit_code = validate_action_groups(tfvars, raw_secret=secret_val)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "::error::" not in captured.out
    assert "::add-mask::/subscriptions/s1/resourceGroups/r1/providers/Microsoft.Insights/actionGroups/ag1" in captured.out
    assert "Successfully validated Action Group configuration: 1 Action Group resource ID(s)" in captured.out

    # Secret ID must not be leaked in diagnostic message
    assert "ag1 configured" not in captured.out

    content = env_file.read_text()
    assert f"TF_VAR_dmd_cache_alert_action_group_ids={json.dumps(json.loads(secret_val))}" in content
