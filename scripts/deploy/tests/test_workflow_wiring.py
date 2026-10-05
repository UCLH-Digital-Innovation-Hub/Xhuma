import yaml


def load_workflow(filename):
    with open(f".github/workflows/{filename}", "r") as f:
        return yaml.safe_load(f)


def test_infra_guard_script_working_directory():
    workflow = load_workflow("infra.yml")
    plan_job = workflow["jobs"]["terraform"]

    guard_step = None
    for step in plan_job["steps"]:
        if step.get("name") == "Validate Branch and Event":
            guard_step = step
            break

    assert guard_step is not None
    assert guard_step.get("working-directory") == "."
    assert "scripts/deploy/validate_branch.py" in guard_step.get("run", "")


def test_legacy_refs_cannot_reach_azure():
    workflow = load_workflow("infra.yml")
    plan_job = workflow["jobs"]["terraform"]

    steps = plan_job["steps"]
    validate_idx = -1
    azure_login_idx = -1
    bootstrap_idx = -1
    apply_idx = -1

    for idx, step in enumerate(steps):
        if step.get("name") == "Validate Branch and Event":
            validate_idx = idx
        elif step.get("name") == "Azure Login":
            azure_login_idx = idx
        elif step.get("name") == "Bootstrap Terraform State Storage":
            bootstrap_idx = idx
            assert step.get("if") == "github.event_name != 'pull_request'"
        elif step.get("name") == "Terraform Apply":
            apply_idx = idx
            assert "github.event_name == 'push'" in step.get("if", "")

    assert validate_idx != -1
    assert azure_login_idx != -1
    assert bootstrap_idx != -1
    assert apply_idx != -1

    # branch validation precedes Azure login/bootstrap/deployment
    assert validate_idx < azure_login_idx
    assert azure_login_idx < bootstrap_idx
    assert bootstrap_idx < apply_idx


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
