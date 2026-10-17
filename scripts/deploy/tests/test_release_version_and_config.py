"""Tests for Xhuma 1.0.0 release version uplift and dm+d target configuration."""

import re
import tomllib
from pathlib import Path

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[3]


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


def test_pyproject_version():
    pyproject_path = REPO_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["version"] == "1.0.0"


def test_fastapi_app_version():
    assert app.version == "1.0.0"


def test_terraform_app_version_default():
    var_file = REPO_ROOT / "infra" / "variables.tf"
    with open(var_file, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'variable\s+"app_version"\s*\{[^}]*default\s*=\s*"([^"]+)"', content)
    assert match is not None
    assert match.group(1) == "1.0.0"


def test_tfvars_versions():
    play_vars = parse_tfvars(REPO_ROOT / "infra" / "env" / "play.tfvars")
    int_vars = parse_tfvars(REPO_ROOT / "infra" / "env" / "int.tfvars")
    prd_vars = parse_tfvars(REPO_ROOT / "infra" / "env" / "prd.tfvars")

    assert play_vars.get("app_version") == "1.0.0"
    assert int_vars.get("app_version") == "1.0.0"
    assert prd_vars.get("app_version") == "1.0.0"


def test_dmd_cache_global_default_remains_false():
    var_file = REPO_ROOT / "infra" / "variables.tf"
    with open(var_file, "r", encoding="utf-8") as f:
        content = f.read()

    match_warm = re.search(r'variable\s+"dmd_cache_warm_enabled"\s*\{[^}]*default\s*=\s*(true|false)', content)
    assert match_warm is not None
    assert match_warm.group(1).lower() == "false", "Global default dmd_cache_warm_enabled must remain false"

    match_sched = re.search(r'variable\s+"dmd_cache_schedule_enabled"\s*\{[^}]*default\s*=\s*(true|false)', content)
    assert match_sched is not None
    assert match_sched.group(1).lower() == "false", "Global default dmd_cache_schedule_enabled must remain false"


def test_play_warmer_config():
    play_vars = parse_tfvars(REPO_ROOT / "infra" / "env" / "play.tfvars")
    assert play_vars.get("dmd_cache_warm_enabled") is True
    assert play_vars.get("dmd_cache_schedule_enabled") is False


def test_int_warmer_config():
    int_vars = parse_tfvars(REPO_ROOT / "infra" / "env" / "int.tfvars")
    assert int_vars.get("dmd_cache_warm_enabled") is True
    assert int_vars.get("dmd_cache_schedule_enabled") is True
    assert int_vars.get("dmd_cache_warm_minute") == 0
    assert int_vars.get("dmd_cache_schedule_start_time") == "2026-10-12T05:00:00"
    # INT inherits default terminology endpoint (no override in int.tfvars)
    assert "dmd_base_url" not in int_vars


def test_prd_warmer_config():
    prd_vars = parse_tfvars(REPO_ROOT / "infra" / "env" / "prd.tfvars")
    assert prd_vars.get("dmd_cache_warm_enabled") is True
    assert prd_vars.get("dmd_cache_schedule_enabled") is True
    assert prd_vars.get("dmd_cache_warm_minute") == 0
    assert prd_vars.get("dmd_cache_schedule_start_time") == "2026-10-12T05:00:00"
    # PRD retains explicit production direct-care endpoint
    assert prd_vars.get("dmd_base_url") == "https://ontology.nhs.uk/production2/fhir"


def test_scheduler_terraform_recurrence_and_invariants():
    main_tf = (REPO_ROOT / "infra" / "main.tf").read_text(encoding="utf-8")

    # Trigger custom recurrence details
    assert 'frequency = "Week"' in main_tf
    assert "interval  = 1" in main_tf
    assert 'timeZone  = "GMT Standard Time"' in main_tf
    assert 'hours    = [5]' in main_tf
    assert 'weekDays = ["Monday"]' in main_tf
    assert "minutes  = [var.dmd_cache_warm_minute]" in main_tf

    # Invariants and alerts
    assert "azurerm_monitor_metric_alert" in main_tf
    assert "la_runs_failed" in main_tf
    assert "la_triggers_failed" in main_tf
    assert "azurerm_monitor_scheduled_query_rules_alert_v2" in main_tf
    assert "dmd_warm_watchdog" in main_tf

    # Check scheduler blockers are replaced by watchdog requirement
    assert "!var.dmd_cache_schedule_enabled\n      error_message = \"dmd_cache_schedule_enabled remains blocked" not in main_tf
    assert "length(azurerm_monitor_scheduled_query_rules_alert_v2.dmd_warm_watchdog) > 0" in main_tf
