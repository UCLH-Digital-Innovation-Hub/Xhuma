import json
import subprocess


def run_script(branch):
    result = subprocess.run(
        ["python", "scripts/deploy/select_targets.py", "infra/targets.json", branch], capture_output=True, text=True
    )
    return result


def test_integration_branch_selects_only_play():
    res = run_script("rehearsal/play-deployment")
    assert res.returncode == 0
    targets = json.loads(res.stdout)
    assert len(targets) == 1
    assert targets[0]["stage"] == "play"


def test_unrelated_feature_branch_rejected():
    res = run_script("feat/random-unrelated-branch")
    assert res.returncode == 1
    assert "No valid, enabled targets found" in res.stderr


def test_integration_branch_rejects_int_production():
    # If the integration branch tried to select INT or PRD, it wouldn't happen because targets.json
    # defines play, and the branch mapping restricts it.
    # We prove it by checking the selected targets only contain play.
    res = run_script("rehearsal/play-deployment")
    targets = json.loads(res.stdout)
    for t in targets:
        assert t["stage"] not in ["int", "prd"]
