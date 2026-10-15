"""Exercise release decisions and retry failures without accessing Azure or GHCR."""

import importlib
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
state = importlib.import_module("deployment_state")
plans = importlib.import_module("saved_plan")
images = importlib.import_module("release_image")
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
        "GITHUB_RUN_NUMBER": "10",
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


def test_app_and_other_target_changes_do_not_require_infra(context):
    root, target, _, commit, invoke = context
    invoke(state, "record-infra")
    original = state.infra_fingerprint(target)
    (root / "app/main.py").write_text("app change")
    (root / "infra/env/prd.tfvars").write_text("prd only")
    commit()
    assert state.infra_fingerprint(target) == original
    invoke(state, "detect")
    assert "infra_needed=false" in (root / "output").read_text()


def test_failed_infra_followed_by_app_commit_remains_pending(context):
    root, _, _, commit, invoke = context
    invoke(state, "record-infra")
    (root / "infra/main.tf").write_text("pending infrastructure")
    commit()
    (root / "app/main.py").write_text("subsequent app change")
    commit()
    invoke(state, "detect")
    assert "infra_needed=true" in (root / "output").read_text()


def test_missing_checkpoint_requires_infra_and_wrong_identity_fails(context):
    root, _, blobs, _, invoke = context
    invoke(state, "detect")
    assert "infra_needed=true" in (root / "output").read_text()
    blobs["tfstate", "deployments/play/infra-success.json"] = b'{"target_id":"prd"}'
    with pytest.raises(ValueError, match="checkpoint mismatch"):
        invoke(state, "detect")


def test_checkpoint_storage_error_propagates(context, monkeypatch):
    class Broken:
        def __init__(self, _):
            raise RuntimeError("access denied")

    monkeypatch.setattr(state, "BlobStore", Broken)
    with pytest.raises(RuntimeError, match="access denied"):
        context[-1](state, "detect")


def test_apply_receipt_is_bound_to_original_plan(context):
    root, _, _, _, invoke = context
    manifest = root / "infra/manifest.json"
    manifest.write_text(json.dumps({"plan_sha256": "a" * 64, "candidate_image_digest": DIGEST}))
    invoke(state, "record-workload")
    invoke(state, "workload-status")
    assert "already_applied=true" in (root / "output").read_text()
    manifest.write_text(json.dumps({"plan_sha256": "b" * 64, "candidate_image_digest": DIGEST}))
    with pytest.raises(ValueError, match="plan_sha256"):
        invoke(state, "workload-status")


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


def test_force_rebuild_still_reuses_original_run_image(context, monkeypatch):
    monkeypatch.setenv("FORCE_REBUILD", "true")
    monkeypatch.setattr(images, "fingerprint", lambda *args: "fingerprint")
    calls = []

    def registry(image, tag):
        calls.append(tag)
        return DIGEST

    monkeypatch.setattr(images, "registry_digest", registry)
    monkeypatch.setattr(sys, "argv", ["image", "resolve"])
    images.main()
    assert calls == ["run-123"]
    assert DIGEST in (context[0] / "output").read_text()


def test_unchanged_app_uses_input_tag(context, monkeypatch):
    monkeypatch.setattr(images, "fingerprint", lambda *args: "fingerprint")
    calls = []

    def registry(image, tag):
        calls.append(tag)
        return DIGEST if tag.startswith("input-") else ""

    monkeypatch.setattr(images, "registry_digest", registry)
    monkeypatch.setattr(sys, "argv", ["image", "resolve"])
    images.main()
    assert calls == ["run-123", "input-fingerprint"]


def test_registry_auth_failure_is_not_cache_miss(monkeypatch):
    def forbidden(*args, **kwargs):
        raise urllib.error.HTTPError("https://ghcr.io/", 403, "Forbidden", None, None)

    monkeypatch.setattr(images.urllib.request, "urlopen", forbidden)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        images.request_json("https://ghcr.io/", missing_ok=True)


def test_release_gate_and_skipped_infrastructure_wiring():
    parent = yaml.safe_load(Path(".github/workflows/matrix-deploy.yml").read_text())["jobs"]
    child = yaml.safe_load(Path(".github/workflows/deploy-target.yml").read_text())["jobs"]
    assert "needs" not in parent["build"]
    assert set(parent["rollout"]["needs"]) == {"ci", "build", "prepare-targets"}
    assert parent["rollout"]["with"]["digest"] == "${{ needs.build.outputs.digest }}"
    assert "needs.infra-apply.result == 'skipped'" in child["deploy"]["if"]
    assert "needs.infra-plan.result == 'success'" in child["deploy"]["if"]
    assert child["infra-apply"]["if"] == "needs.infra-plan.outputs.infra_needed == 'true'"
    apply_names = [s.get("name") for s in child["infra-apply"]["steps"]]
    assert apply_names.index("Terraform Init") < apply_names.index("Download and Verify Plan")


def test_older_run_cannot_overwrite_newer_checkpoint(context, monkeypatch):
    _, _, _, _, invoke = context
    invoke(state, "record-infra")
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "9")
    with pytest.raises(ValueError, match="newer run"):
        invoke(state, "detect")


def test_force_infra_overrides_matching_checkpoint(context, monkeypatch):
    root, _, _, _, invoke = context
    invoke(state, "record-infra")
    monkeypatch.setattr(sys, "argv", ["state", "detect", "--target", "play", "--force"])
    state.main()
    assert "infra_needed=true" in (root / "output").read_text()


def test_saved_release_record_wins_over_registry(context, monkeypatch):
    root, _, _, _, _ = context
    monkeypatch.setattr(images, "fingerprint", lambda *args: "fingerprint")
    monkeypatch.setattr(sys, "argv", ["image", "record"])
    images.main()
    monkeypatch.setattr(images, "registry_digest", lambda *args: pytest.fail("must use original record"))
    monkeypatch.setattr(sys, "argv", ["image", "resolve"])
    images.main()
    record = json.loads((root / "release-image.json").read_text())
    record["workflow_run_id"] = "different-run"
    (root / "release-image.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="does not match"):
        images.main()
