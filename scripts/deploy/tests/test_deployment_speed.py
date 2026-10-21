"""Exercise saved-plan and apply-retry safeguards without accessing Azure."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
state = importlib.import_module("deployment_state")
plans = importlib.import_module("saved_plan")
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state, "ROOT", tmp_path)
    monkeypatch.setattr(plans, "ROOT", tmp_path)
    for path in [
        "infra/main.tf",
        "infra/env/play.tfvars",
        "infra/env/prd.tfvars",
        "infra/backends/play.hcl",
        "infra/shared/main.tf",
        "infra/shared/backends/shared.hcl",
        "app/main.py",
    ]:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("initial\n")
    subprocess.run(["git", "init", "-q"], check=True)

    def commit():
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "test"], check=True
        )

    commit()
    target = {
        "id": "play",
        "tfvars_file": "infra/env/play.tfvars",
        "backend_file": "infra/backends/play.hcl",
        "shared_backend_file": "backends/shared.hcl",
        "require_existing_state": True,
    }
    monkeypatch.setattr(state, "target_config", lambda _: target)
    monkeypatch.setattr(plans, "target_config", lambda _: target)
    monkeypatch.setattr(
        state,
        "backend_coordinates",
        lambda _: {
            "resource_group_name": "rg",
            "storage_account_name": "store",
            "container_name": "tfstate",
            "key": "play.tfstate",
        },
    )
    for key, value in {
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_SHA": "b" * 40,
        "GITHUB_RUN_ID": "123",
        "ARM_SUBSCRIPTION_ID": "sub",
        "ARM_TENANT_ID": "tenant",
        "IMAGE_NAME": "org/repo",
        "IMAGE_DIGEST": DIGEST,
        "TF_VAR_docker_image": "org/repo@" + DIGEST,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_ENV": str(tmp_path / "env"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }.items():
        monkeypatch.setenv(key, value)
    blobs = {}

    class Store:
        def __init__(self, _):
            self.coords = state.backend_coordinates("")

        def exists(self, name, container=None):
            return (container or "tfstate", name) in blobs

        def read(self, name, container=None):
            item = blobs.get((container or "tfstate", name))
            return json.loads(item) if item is not None else None

        def write(self, name, record, overwrite=True):
            assert overwrite or not self.exists(name)
            blobs["tfstate", name] = json.dumps(record).encode()

        def command(self, action, name, container=None, **kwargs):
            key = (container or "tfstate", name)
            if action == "upload":
                assert kwargs["overwrite"] == "false" and key not in blobs
                blobs[key] = Path(kwargs["file"]).read_bytes()
            elif action == "download":
                Path(kwargs["file"]).write_bytes(blobs[key])
            else:
                raise AssertionError(action)

    monkeypatch.setattr(state, "BlobStore", Store)
    monkeypatch.setattr(plans, "BlobStore", Store)

    def invoke(module, action):
        monkeypatch.setattr(sys, "argv", ["script", action, "--target", "play"])
        module.main()

    return tmp_path, target, blobs, commit, invoke


def test_receipt_storage_error_propagates(context, monkeypatch):
    class Broken:
        def __init__(self, _):
            raise RuntimeError("access denied")

    monkeypatch.setattr(state, "BlobStore", Broken)
    with pytest.raises(RuntimeError, match="access denied"):
        context[-1](state, "workload-status")


@pytest.fixture
def workload(context):
    root, _, _, _, _ = context
    (root / "infra/manifest.json").write_text(json.dumps({"plan_sha256": "a" * 64, "candidate_image_digest": DIGEST}))
    return context


def test_missing_receipt_requires_workload_apply(workload):
    root, _, _, _, invoke = workload
    invoke(state, "workload-status")
    assert (root / "output").read_text() == "already_applied=false\n"


def test_apply_receipt_is_scoped_to_one_run(workload, monkeypatch):
    root, _, blobs, _, invoke = workload
    invoke(state, "record-workload")
    assert set(blobs) == {("tfstate", "deployments/play/runs/123/workload-applied.json")}
    monkeypatch.setenv("GITHUB_RUN_ID", "124")
    invoke(state, "workload-status")
    assert (root / "output").read_text() == "already_applied=false\n"


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("GITHUB_REPOSITORY", "other/repo", "repository"),
        ("GITHUB_SHA", "c" * 40, "source_commit_sha"),
        ("ARM_SUBSCRIPTION_ID", "other-sub", "azure_subscription_id"),
        ("ARM_TENANT_ID", "other-tenant", "azure_tenant_id"),
    ],
)
def test_apply_receipt_rejects_changed_identity(workload, monkeypatch, variable, value, field):
    invoke = workload[-1]
    invoke(state, "record-workload")
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=field):
        invoke(state, "workload-status")


def test_apply_receipt_rejects_changed_infrastructure_inputs(workload):
    root, _, _, commit, invoke = workload
    invoke(state, "record-workload")
    (root / "infra/main.tf").write_text("changed infrastructure")
    commit()
    with pytest.raises(ValueError, match="input_hash"):
        invoke(state, "workload-status")


def test_apply_receipt_is_bound_to_original_plan(workload):
    root, _, _, _, invoke = workload
    manifest = root / "infra/manifest.json"
    invoke(state, "record-workload")
    invoke(state, "workload-status")
    assert "already_applied=true" in (root / "output").read_text()
    manifest.write_text(json.dumps({"plan_sha256": "b" * 64, "candidate_image_digest": DIGEST}))
    with pytest.raises(ValueError, match="plan_sha256"):
        invoke(state, "workload-status")


def test_apply_receipt_rejects_changed_image(workload):
    root, _, _, _, invoke = workload
    invoke(state, "record-workload")
    (root / "infra/manifest.json").write_text(
        json.dumps({"plan_sha256": "a" * 64, "candidate_image_digest": "sha256:" + "c" * 64})
    )
    with pytest.raises(ValueError, match="candidate_image_digest"):
        invoke(state, "workload-status")


def test_apply_receipt_read_failure_is_not_treated_as_missing(workload, monkeypatch):
    class BrokenRead(state.BlobStore):
        def read(self, name, container=None):
            raise RuntimeError("read denied")

    monkeypatch.setattr(state, "BlobStore", BrokenRead)
    with pytest.raises(RuntimeError, match="read denied"):
        workload[-1](state, "workload-status")


def test_apply_receipt_write_failure_propagates(workload, monkeypatch):
    class BrokenWrite(state.BlobStore):
        def write(self, name, record, overwrite=True):
            raise RuntimeError("write denied")

    monkeypatch.setattr(state, "BlobStore", BrokenWrite)
    with pytest.raises(RuntimeError, match="write denied"):
        workload[-1](state, "record-workload")
    assert not workload[2]


@pytest.fixture
def planned(context, monkeypatch):
    root, target, blobs, _, invoke = context
    content = {"variables": {"docker_image": {"value": "org/repo@" + DIGEST}}, "resource_changes": []}
    original_run = subprocess.run

    def tf_run(args, **kwargs):
        if args[0] != "terraform":
            return original_run(args, **kwargs)
        assert args[:2] == ["terraform", "plan"]
        Path(next(x[5:] for x in args if x.startswith("-out="))).write_bytes(b"original-plan")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(plans.subprocess, "run", tf_run)
    monkeypatch.setattr(plans, "terraform_json", lambda _: content)
    return root, target, blobs, invoke, content


def test_plan_reuse_verifies_original_manifest_and_never_replans(planned, monkeypatch):
    root, _, blobs, invoke, _ = planned
    invoke(plans, "plan")
    original = dict(blobs)
    original_run = subprocess.run

    def no_plan(args, **kwargs):
        if args[0] != "terraform":
            return original_run(args, **kwargs)
        raise AssertionError("must not replan")

    monkeypatch.setattr(plans.subprocess, "run", no_plan)
    invoke(plans, "plan")
    invoke(plans, "download")
    assert blobs == original
    name = next(name for container, name in blobs if name.endswith(".tfplan"))
    blobs["tfplans", name] = b"tampered"
    with pytest.raises(ValueError, match="checksum"):
        invoke(plans, "plan")
    assert (root / "infra/manifest.json").exists()


def test_incomplete_pair_is_rejected(planned):
    _, target, blobs, invoke, _ = planned
    blobs["tfplans", f"{target['id']}-{'b' * 40}-123.tfplan"] = b"orphan"
    with pytest.raises(ValueError, match="Incomplete"):
        invoke(plans, "plan")


def test_destructive_plan_never_published(planned):
    _, _, blobs, invoke, content = planned
    content["resource_changes"] = [{"address": "db", "change": {"actions": ["delete", "create"]}}]
    with pytest.raises(ValueError, match="Migration guard"):
        invoke(plans, "plan")
    assert not blobs


def test_download_never_creates_replacement_plan(planned):
    with pytest.raises(ValueError, match="refusing to generate"):
        planned[3](plans, "download")


def test_saved_plan_cannot_change_candidate_image(planned, monkeypatch):
    _, _, _, invoke, _ = planned
    invoke(plans, "plan")
    monkeypatch.setenv("IMAGE_DIGEST", "sha256:" + "c" * 64)
    with pytest.raises(ValueError, match="candidate_image_digest"):
        invoke(plans, "plan")


def test_release_gate_and_unconditional_infrastructure_wiring():
    parent = yaml.safe_load(Path(".github/workflows/matrix-deploy.yml").read_text())["jobs"]
    child = yaml.safe_load(Path(".github/workflows/deploy-target.yml").read_text())["jobs"]
    assert "needs" not in parent["build"]
    assert set(parent["rollout"]["needs"]) == {"ci", "build", "prepare-targets"}
    assert parent["rollout"]["with"]["digest"] == "${{ needs.build.outputs.digest }}"
    assert parent["build"]["outputs"]["digest"] == "${{ steps.build.outputs.digest }}"
    assert "if" not in child["infra-plan"]
    assert "if" not in child["infra-apply"]
    assert "if" not in child["deploy"]
    assert set(child["infra-apply"]["needs"]) == {"infra-plan"}
    assert set(child["deploy"]["needs"]) == {"infra-apply"}
    plan_step = next(step for step in child["infra-plan"]["steps"] if step.get("name") == "Terraform Plan")
    assert "if" not in plan_step
    apply_names = [s.get("name") for s in child["infra-apply"]["steps"]]
    assert apply_names.index("Terraform Init") < apply_names.index("Download and Verify Plan")
