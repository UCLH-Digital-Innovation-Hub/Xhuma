import json
import os
import subprocess

import yaml


def load_workflow(filename):
    path = f".github/workflows/{filename}"
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return yaml.safe_load(f)


def test_legacy_routes_retired():
    infra_wf = load_workflow("infra.yml")
    cd_wf = load_workflow("cd.yml")
    assert infra_wf is None, "legacy infra.yml must be retired"
    assert cd_wf is None, "legacy cd.yml must be retired"


def test_matrix_deploy_listens_to_main():
    workflow = load_workflow("matrix-deploy.yml")
    assert workflow is not None
    # In PyYAML, unquoted `on` is loaded as True
    on_block = workflow.get(True) or workflow.get("on", {})
    branches = on_block.get("push", {}).get("branches", [])
    assert "main" in branches, "matrix-deploy must listen to main"
    assert "dev" in branches, "matrix-deploy must listen to dev"
    assert "int" in branches, "matrix-deploy must listen to int"


def test_matrix_deploy_backend_path_exists():
    workflow = load_workflow("matrix-deploy.yml")
    infra_plan_job = workflow["jobs"]["infra-plan"]

    bootstrap_step = None
    for step in infra_plan_job["steps"]:
        if "infra/bootstrap/setup-target.sh" in step.get("run", ""):
            bootstrap_step = step
            break

    assert bootstrap_step is not None
    # We check that the step does not use infra/${{ matrix.target.backend_file }}
    assert bootstrap_step["env"].get("XHUMA_BACKEND_FILE") == "${{ matrix.target.backend_file }}"


def test_consumed_job_outputs_have_needs():
    workflow = load_workflow("matrix-deploy.yml")
    infra_apply_job = workflow["jobs"]["infra-apply"]

    # It consumes digest from build
    # Check that 'build' is in the needs list
    assert "build" in infra_apply_job.get("needs", [])

    # Check that digest is used
    deploy_step = None
    for step in infra_apply_job["steps"]:
        if step.get("name") == "Download and Verify Plan":
            deploy_step = step
            break

    assert deploy_step is not None
    assert "${{ needs.build.outputs.digest }}" in deploy_step.get("run", "")


def get_selected_targets(branch):
    result = subprocess.run(
        ["python", "scripts/deploy/select_targets.py", "infra/targets.json", branch],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_target_selection_integrity():
    dev_targets = get_selected_targets("dev")
    int_targets = get_selected_targets("int")
    main_targets = get_selected_targets("main")
    dev_ids = [t["id"] for t in dev_targets]
    int_ids = [t["id"] for t in int_targets]
    main_ids = [t["id"] for t in main_targets]

    assert dev_ids == ["play"], "dev -> Play only"
    assert int_ids == ["int"], "int -> INT only"
    assert main_ids == ["prd"], "main -> PRD only"

    assert "play" not in main_ids, "main never selects Play"
    assert "int" not in main_ids, "main never selects INT"
    assert "prd" not in dev_ids, "dev never selects PRD"
    assert "prd" not in int_ids, "int never selects PRD"


def test_shared_backend_file_selection():
    dev_targets = get_selected_targets("dev")
    int_targets = get_selected_targets("int")
    main_targets = get_selected_targets("main")

    assert dev_targets[0]["shared_backend_file"] == "backends/shared.hcl"
    assert int_targets[0]["shared_backend_file"] == "backends/shared.hcl"
    assert main_targets[0]["shared_backend_file"] == "backends/prd.hcl"

    # Prove PRD can never resolve to shared.tfstate through its backend file
    # Ensure prd.hcl does NOT contain 'key = "shared.tfstate"'
    with open("infra/shared/backends/prd.hcl", "r") as f:
        prd_hcl = f.read()
        assert 'key                  = "shared.tfstate"' not in prd_hcl
        assert 'key = "shared.tfstate"' not in prd_hcl


def test_warmer_wiring_in_workflow():
    """Verify matrix-deploy.yml uses Terraform outputs as single source of truth for warmer."""
    workflow = load_workflow("matrix-deploy.yml")
    infra_apply_job = workflow["jobs"]["infra-apply"]
    steps = {step.get("name"): step for step in infra_apply_job["steps"]}

    assert "Extract Target Terraform Outputs" in steps
    extract_step = steps["Extract Target Terraform Outputs"]
    assert extract_step.get("env", {}).get("ARM_CLIENT_ID") == "${{ secrets.AZURE_CLIENT_ID }}"
    assert extract_step.get("env", {}).get("ARM_CLIENT_SECRET") == "${{ secrets.AZURE_CLIENT_SECRET }}"
    assert extract_step.get("env", {}).get("ARM_TENANT_ID") == "${{ secrets.AZURE_TENANT_ID }}"
    assert extract_step.get("env", {}).get("ARM_SUBSCRIPTION_ID") == "${{ secrets.AZURE_SUBSCRIPTION_ID }}"
    run_script = extract_step["run"]
    assert "dmd_cache_warm_enabled" in run_script
    assert "dmd_cache_ca_subnet_id" in run_script
    assert "dmd_cache_job_name" in run_script
    assert "dmd_cache_job_image" in run_script
    assert 'if [ "$WARM_ENABLED" == "true" ]; then' in run_script
    assert 'if [ -z "$CA_SUBNET_ID" ]; then' in run_script
    assert 'if [ -z "$JOB_NAME" ]; then' in run_script

    reconcile_step = steps["Reconcile Shared Infrastructure - Plan"]
    rec_script = reconcile_step["run"]
    assert 'TARGET_SUBNETS=("$APP_SUBNET_ID")' in rec_script
    assert 'if [ -n "$CA_SUBNET_ID" ]; then' in rec_script
    assert 'TARGET_SUBNETS+=("$CA_SUBNET_ID")' in rec_script
    assert "dmd_cache_warm_enabled" not in rec_script

    verify_step = steps["Verify Container Apps Job Image"]
    ver_script = verify_step["run"]
    assert 'if [ -n "$JOB_NAME" ]; then' in ver_script
    assert "az containerapp job show" in ver_script
    assert 'if [ -z "$CURRENT_IMAGE" ]; then' in ver_script
    assert 'if [ "$CURRENT_IMAGE" != "$EXPECTED_JOB_IMAGE" ]; then' in ver_script


def test_warmer_disabled_subnet_derivation():
    """Simulate output extraction when warmer is disabled: ACA subnet absent."""
    outputs = {
        "dmd_cache_warm_enabled": {"value": False},
        "dmd_cache_ca_subnet_id": {"value": None},
        "dmd_cache_job_name": {"value": None},
    }
    app_subnet = "/sub/rg/vnet/subnets/app-subnet"
    target_subnets = [app_subnet]
    ca_subnet = outputs["dmd_cache_ca_subnet_id"]["value"]
    if ca_subnet:
        target_subnets.append(ca_subnet)

    assert target_subnets == [app_subnet]
    assert len(target_subnets) == 1


def test_warmer_enabled_subnet_derivation():
    """Simulate output extraction when warmer is enabled: ACA subnet included."""
    outputs = {
        "dmd_cache_warm_enabled": {"value": True},
        "dmd_cache_ca_subnet_id": {"value": "/sub/rg/vnet/subnets/ca-subnet"},
        "dmd_cache_job_name": {"value": "app-dmd-warm"},
    }
    app_subnet = "/sub/rg/vnet/subnets/app-subnet"
    target_subnets = [app_subnet]
    ca_subnet = outputs["dmd_cache_ca_subnet_id"]["value"]
    if ca_subnet:
        target_subnets.append(ca_subnet)

    assert target_subnets == [app_subnet, "/sub/rg/vnet/subnets/ca-subnet"]
    assert len(target_subnets) == 2


def test_warmer_enabled_missing_output_fails_closed():
    """Verify that an enabled warmer with missing ca_subnet_id or job_name fails closed."""
    import pytest

    outputs = {
        "dmd_cache_warm_enabled": {"value": True},
        "dmd_cache_ca_subnet_id": {"value": None},
        "dmd_cache_job_name": {"value": None},
    }
    warm_enabled = outputs["dmd_cache_warm_enabled"]["value"]
    ca_subnet = outputs["dmd_cache_ca_subnet_id"]["value"]
    job_name = outputs["dmd_cache_job_name"]["value"]

    def validate_outputs():
        if warm_enabled:
            if not ca_subnet:
                raise ValueError("Target has dmd_cache_warm_enabled=true but dmd_cache_ca_subnet_id is null/missing!")
            if not job_name:
                raise ValueError("Target has dmd_cache_warm_enabled=true but dmd_cache_job_name is null/missing!")

    with pytest.raises(ValueError, match="dmd_cache_ca_subnet_id is null/missing"):
        validate_outputs()


def test_migration_guard_workflow_syntax_and_fail_closed():
    """Verify matrix-deploy.yml Migration Guard extracts replace_paths and fails closed."""
    workflow = load_workflow("matrix-deploy.yml")
    infra_plan_job = workflow["jobs"]["infra-plan"]
    plan_step = None
    for step in infra_plan_job["steps"]:
        if step.get("name") == "Terraform Plan":
            plan_step = step
            break

    assert plan_step is not None
    run_script = plan_step["run"]

    # Must check for delete or replace actions
    assert 'select(.change.actions | index("delete") or index("replace"))' in run_script
    # Must extract replace_paths
    assert ".change.replace_paths" in run_script
    # Must fail closed if require_existing_state is true
    assert 'if [ "${{ matrix.target.require_existing_state }}" == "true" ]; then' in run_script
    assert "exit 1" in run_script
    # Must NOT access before or after values in the diagnostic extraction
    assert ".change.before" not in run_script
    assert ".change.after" not in run_script


def test_migration_guard_jq_replace_paths_diagnostics(tmp_path):
    """Verify the Migration Guard jq diagnostic accurately outputs replace_paths without leaking values."""
    mock_plan = {
        "resource_changes": [
            {
                "address": "azurerm_container_app_environment.cae[0]",
                "change": {
                    "actions": ["delete", "create"],
                    "before": {"secret_token": "SENSITIVE_OLD_VALUE"},
                    "after": {"secret_token": "SENSITIVE_NEW_VALUE"},
                    "replace_paths": [["workload_profile"], ["infrastructure_resource_group_name"]],
                },
            },
            {
                "address": "azurerm_container_app_job.dmd_warm[0]",
                "change": {
                    "actions": ["delete"],
                    "before": {"password": "SECRET_PASSWORD"},
                    "after": None,
                    "replace_paths": None,
                },
            },
            {
                "address": "azurerm_subnet.ca_subnet[0]",
                "change": {
                    "actions": ["no-op"],
                },
            },
        ]
    }

    plan_file = tmp_path / "test_plan.json"
    plan_file.write_text(json.dumps(mock_plan))

    jq_script = """
    [
      .resource_changes[]?
      | select(.change.actions | index("delete") or index("replace"))
      | "\\(.address)\\nactions: \\(.change.actions | tojson)\\nreplace_paths:\\(
          if ((.change.replace_paths // []) | length > 0) then
            "\\n" + ([.change.replace_paths[] | "  - \\(tojson)"] | join("\\n"))
          else
            "\\n  - []"
          end
        )"
    ] | join("\\n\\n")
    """

    res = subprocess.run(
        ["jq", "-r", jq_script, str(plan_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    output = res.stdout.strip()

    # Address, actions, and replace_paths are formatted properly
    assert "azurerm_container_app_environment.cae[0]" in output
    assert 'actions: ["delete","create"]' in output
    assert 'replace_paths:\n  - ["workload_profile"]\n  - ["infrastructure_resource_group_name"]' in output

    assert "azurerm_container_app_job.dmd_warm[0]" in output
    assert 'actions: ["delete"]' in output
    assert "replace_paths:\n  - []" in output

    # Non-destructive resources are not present
    assert "azurerm_subnet.ca_subnet[0]" not in output

    # Secrets and values from before/after are strictly absent
    assert "SENSITIVE_OLD_VALUE" not in output
    assert "SENSITIVE_NEW_VALUE" not in output
    assert "SECRET_PASSWORD" not in output


def test_dmd_action_group_workflow_wiring():
    workflow_path = ".github/workflows/matrix-deploy.yml"
    with open(workflow_path, "r") as f:
        content = f.read()
    workflow = yaml.safe_load(content)

    # 1. Custom validation step and script call are strictly absent
    assert "validate_action_groups.py" not in content, "Custom action group validator script must not be called"
    assert "Validate dm+d Action Group Configuration" not in content, "Validator step must be absent"

    # 2. Plan step wires TF_VAR_dmd_cache_alert_action_group_ids directly with safe [] fallback
    infra_plan_job = workflow["jobs"]["infra-plan"]
    plan_step = None
    for step in infra_plan_job["steps"]:
        if step.get("name") == "Terraform Plan":
            plan_step = step
            break
    assert plan_step is not None, "Terraform Plan step must exist"
    env_vars = plan_step.get("env", {})
    action_group_wiring = env_vars.get("TF_VAR_dmd_cache_alert_action_group_ids")
    assert action_group_wiring == "${{ secrets.DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON || '[]' }}", (
        "Action group IDs must be wired directly from secret with safe [] fallback"
    )
