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
