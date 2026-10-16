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
    workflow = load_workflow("deploy-target.yml")
    infra_plan_job = workflow["jobs"]["infra-plan"]

    bootstrap_step = None
    for step in infra_plan_job["steps"]:
        if "infra/bootstrap/setup-target.sh" in step.get("run", ""):
            bootstrap_step = step
            break

    assert bootstrap_step is not None
    # We check that the step does not use infra/${{ matrix.target.backend_file }}
    assert bootstrap_step["env"].get("XHUMA_BACKEND_FILE") == "${{ fromJSON(inputs.target).backend_file }}"


def test_consumed_job_outputs_have_needs():
    workflow = load_workflow("matrix-deploy.yml")
    rollout = workflow["jobs"]["rollout"]
    assert "build" in rollout["needs"]
    assert rollout["with"]["digest"] == "${{ needs.build.outputs.digest }}"
    target = load_workflow("deploy-target.yml")
    inputs = (target.get(True) or target["on"])["workflow_call"]["inputs"]
    assert inputs["digest"]["required"] is True


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
