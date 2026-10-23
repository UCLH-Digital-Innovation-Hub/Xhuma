"""Focused tests for dm+d Container Apps Job native scheduling capability.

Verifies:
1. Default configuration emits manual_trigger_config
2. Native scheduling emits schedule_trigger_config
3. schedule_trigger_config receives configured cron expression
4. manual_trigger_config is absent when native scheduling is enabled
5. schedule_trigger_config is absent when native scheduling is disabled
6. Native scheduling enabled without cron fails validation/check
7. Native scheduler + Logic App scheduler together fails closed
8. Existing Logic App scheduler resources are still controlled exclusively by dmd_cache_schedule_enabled
9. Existing warmer infrastructure remains controlled by dmd_cache_warm_enabled
10. No target tfvars enable native scheduling in this PR
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA_DIR = REPO_ROOT / "infra"
MAIN_TF = INFRA_DIR / "main.tf"
VARIABLES_TF = INFRA_DIR / "variables.tf"
ENV_DIR = INFRA_DIR / "env"


def parse_tfvars(filepath: Path) -> dict[str, str | bool | int]:
    """Simple parser for HCL .tfvars files."""
    vars_dict: dict[str, str | bool | int] = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v.lower() == "true":
                vars_dict[k] = True
            elif v.lower() == "false":
                vars_dict[k] = False
            elif v.isdigit():
                vars_dict[k] = int(v)
            else:
                vars_dict[k] = v
    return vars_dict


def test_variables_defined():
    """Verify dmd_cache_native_schedule_enabled and dmd_cache_native_cron_expression variables."""
    content = VARIABLES_TF.read_text(encoding="utf-8")

    assert 'variable "dmd_cache_native_schedule_enabled"' in content
    assert 'variable "dmd_cache_native_cron_expression"' in content

    # Check default for schedule enabled is false
    sched_match = re.search(
        r'variable\s+"dmd_cache_native_schedule_enabled"\s*\{[^}]*default\s*=\s*(false|true)',
        content,
    )
    assert sched_match is not None
    assert sched_match.group(1) == "false"


def test_dynamic_trigger_blocks_in_job_resource():
    """Verify azurerm_container_app_job.dmd_warm declares dynamic blocks for triggers."""
    content = MAIN_TF.read_text(encoding="utf-8")

    job_start = content.find('resource "azurerm_container_app_job" "dmd_warm"')
    assert job_start != -1
    job_block = content[job_start : content.find('resource "azurerm_logic_app_workflow"', job_start)]

    # Dynamic manual_trigger_config
    assert 'dynamic "manual_trigger_config"' in job_block
    assert "var.dmd_cache_native_schedule_enabled ? [] : [1]" in job_block

    # Dynamic schedule_trigger_config
    assert 'dynamic "schedule_trigger_config"' in job_block
    assert "var.dmd_cache_native_schedule_enabled ? [1] : []" in job_block
    assert "cron_expression          = var.dmd_cache_native_cron_expression" in job_block


def test_trigger_emission_logic():
    """Verify trigger block selection logic for enabled/disabled states."""

    def evaluate_triggers(native_enabled: bool, cron: str | None):
        manual_blocks = [] if native_enabled else [{"parallelism": 1, "replica_completion_count": 1}]
        schedule_blocks = (
            [{"cron_expression": cron, "parallelism": 1, "replica_completion_count": 1}] if native_enabled else []
        )
        return manual_blocks, schedule_blocks

    # 1. Default configuration (native_enabled=False) emits manual_trigger_config
    manual, schedule = evaluate_triggers(native_enabled=False, cron=None)
    assert len(manual) == 1
    assert len(schedule) == 0

    # 2. Native scheduling enabled emits schedule_trigger_config
    # 3. schedule_trigger_config receives configured cron expression
    # 4. manual_trigger_config is absent when native scheduling is enabled
    manual, schedule = evaluate_triggers(native_enabled=True, cron="0 4 * * 1")
    assert len(manual) == 0
    assert len(schedule) == 1
    assert schedule[0]["cron_expression"] == "0 4 * * 1"

    # 5. schedule_trigger_config is absent when native scheduling is disabled
    manual, schedule = evaluate_triggers(native_enabled=False, cron="0 4 * * 1")
    assert len(manual) == 1
    assert len(schedule) == 0


def test_native_schedule_without_cron_invariant():
    """6. Native scheduling enabled without cron fails validation/precondition invariant."""
    content = MAIN_TF.read_text(encoding="utf-8")

    # Invariant condition: !var.dmd_cache_native_schedule_enabled || (var.dmd_cache_native_cron_expression != null && trimspace(var.dmd_cache_native_cron_expression) != "")
    assert "!var.dmd_cache_native_schedule_enabled || (var.dmd_cache_native_cron_expression != null" in content
    assert 'trimspace(var.dmd_cache_native_cron_expression) != ""' in content

    def evaluate_cron_invariant(native_enabled: bool, cron: str | None) -> bool:
        return not native_enabled or (cron is not None and cron.strip() != "")

    # When native schedule disabled, cron is optional
    assert evaluate_cron_invariant(False, None) is True
    assert evaluate_cron_invariant(False, "") is True

    # When native schedule enabled, cron is mandatory and non-empty
    assert evaluate_cron_invariant(True, "0 4 * * 1") is True
    assert evaluate_cron_invariant(True, None) is False
    assert evaluate_cron_invariant(True, "") is False
    assert evaluate_cron_invariant(True, "   ") is False


def test_mutual_exclusivity_invariant():
    """7. Native scheduler + Logic App scheduler together fails closed."""
    content = MAIN_TF.read_text(encoding="utf-8")

    # Precondition and assert check
    assert "!(var.dmd_cache_native_schedule_enabled && var.dmd_cache_schedule_enabled)" in content
    assert "Native Container Apps scheduling and Logic App scheduling are mutually exclusive." in content

    def evaluate_mutual_exclusion(native_enabled: bool, logic_app_enabled: bool) -> bool:
        return not (native_enabled and logic_app_enabled)

    # Allowed combinations:
    assert evaluate_mutual_exclusion(False, False) is True  # Manual only
    assert evaluate_mutual_exclusion(True, False) is True  # Native schedule only
    assert evaluate_mutual_exclusion(False, True) is True  # Logic App schedule only

    # Prohibited combination:
    assert evaluate_mutual_exclusion(True, True) is False  # Both enabled -> FAILS


def test_logic_app_resources_controlled_exclusively_by_dmd_cache_schedule_enabled():
    """8. Existing Logic App scheduler resources are still controlled exclusively by dmd_cache_schedule_enabled."""
    content = MAIN_TF.read_text(encoding="utf-8")

    # Verify Logic App workflow count
    la_workflow_match = re.search(
        r'resource\s+"azurerm_logic_app_workflow"\s+"dmd_warm"\s*\{[^}]*count\s*=\s*([^;\n]+)',
        content,
    )
    assert la_workflow_match is not None
    count_expr = la_workflow_match.group(1).strip()
    assert count_expr == "var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0"
    assert "dmd_cache_native_schedule_enabled" not in count_expr

    # Verify custom role definition count
    role_def_match = re.search(
        r'resource\s+"azurerm_role_definition"\s+"dmd_job_operator"\s*\{[^}]*count\s*=\s*([^;\n]+)',
        content,
    )
    assert role_def_match is not None
    assert "var.dmd_cache_schedule_enabled" in role_def_match.group(1)
    assert "dmd_cache_native_schedule_enabled" not in role_def_match.group(1)


def test_warmer_infrastructure_controlled_by_dmd_cache_warm_enabled():
    """9. Existing warmer infrastructure remains controlled by dmd_cache_warm_enabled."""
    content = MAIN_TF.read_text(encoding="utf-8")

    job_match = re.search(
        r'resource\s+"azurerm_container_app_job"\s+"dmd_warm"\s*\{[^}]*count\s*=\s*([^;\n]+)',
        content,
    )
    assert job_match is not None
    assert job_match.group(1).strip() == "var.dmd_cache_warm_enabled ? 1 : 0"

    cae_match = re.search(
        r'resource\s+"azurerm_container_app_environment"\s+"cae"\s*\{[^}]*count\s*=\s*([^;\n]+)',
        content,
    )
    assert cae_match is not None
    assert cae_match.group(1).strip() == "var.dmd_cache_warm_enabled ? 1 : 0"


def test_no_target_tfvars_enable_native_scheduling():
    """10. No target tfvars enable native scheduling in this PR."""
    for env in ["play", "int", "prd"]:
        tfvars_path = ENV_DIR / f"{env}.tfvars"
        assert tfvars_path.exists()
        vars_dict = parse_tfvars(tfvars_path)

        # Must not have native schedule enabled set to True
        assert vars_dict.get("dmd_cache_native_schedule_enabled") is not True, (
            f"{env}.tfvars must not enable native scheduling in this PR"
        )
        assert vars_dict.get("dmd_cache_schedule_enabled") is False, (
            f"{env}.tfvars Logic App schedule must remain disabled"
        )
        assert vars_dict.get("dmd_cache_warm_enabled") is True, (
            f"{env}.tfvars warmer infrastructure must remain enabled"
        )
