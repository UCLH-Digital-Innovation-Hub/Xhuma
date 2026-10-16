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


def test_dependabot_container_uses_unprivileged_pr_event():
    workflow = load_workflow("ci.yml")
    triggers = workflow.get(True) or workflow.get("on", {})
    assert "dev" in triggers["pull_request"]["branches"]
    assert "pull_request_target" not in triggers
    job = workflow["jobs"]["dependabot-container"]
    assert "github.event_name == 'pull_request'" in job["if"]
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in job["if"]
    assert job["permissions"] == {"contents": "read"}
    assert "secrets." not in json.dumps(job)


def test_container_journey_keeps_production_runtime_and_network_isolated():
    with open("docker-compose.smoke.yml") as f:
        stack = yaml.safe_load(f)
    app = stack["services"]["xhuma"]
    assert app["build"] == "."
    assert "command" not in app
    assert "entrypoint" not in app
    assert all("/code" not in mount for mount in app["volumes"])
    assert app["environment"]["ENV"] == "prod"
    assert "AUDIT_FAIL_OPEN" not in app["environment"]
    assert stack["networks"]["default"]["internal"] is True
    assert all("ports" not in service for service in stack["services"].values())


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
