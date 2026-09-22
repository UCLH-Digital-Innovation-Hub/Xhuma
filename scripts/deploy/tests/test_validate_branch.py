import subprocess

SCRIPT_PATH = "scripts/deploy/validate_branch.py"


def run_script(args):
    result = subprocess.run(["python", SCRIPT_PATH] + args, capture_output=True, text=True)
    return result


def test_cd_valid_branch():
    assert run_script(["cd", "push", "refs/heads/main"]).returncode == 0
    assert run_script(["cd", "push", "refs/heads/int"]).returncode == 0


def test_cd_invalid_branch():
    res = run_script(["cd", "workflow_dispatch", "refs/heads/feature/foo"])
    assert res.returncode == 1
    assert "Unsupported branch for CD pipeline" in res.stdout


def test_infra_push_valid_branch():
    assert run_script(["infra", "push", "refs/heads/main"]).returncode == 0
    assert run_script(["infra", "workflow_dispatch", "refs/heads/int"]).returncode == 0


def test_infra_push_invalid_branch():
    res = run_script(["infra", "push", "refs/heads/feature"])
    assert res.returncode == 1
    assert "Unsupported branch for push/dispatch" in res.stdout


def test_infra_pr_valid_base():
    assert run_script(["infra", "pull_request", "refs/pull/1/merge", "main"]).returncode == 0
    assert run_script(["infra", "pull_request", "refs/pull/1/merge", "int"]).returncode == 0


def test_infra_pr_invalid_base():
    res = run_script(["infra", "pull_request", "refs/pull/1/merge", "feature"])
    assert res.returncode == 1
    assert "Unsupported base branch for PR" in res.stdout


def test_invalid_event():
    res = run_script(["infra", "release", "refs/tags/v1.0.0"])
    assert res.returncode == 1
    assert "Unsupported event" in res.stdout
