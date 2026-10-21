"""Publish/retrieve an immutable target plan with its original manifest."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from deployment_state import ROOT, BlobStore, infra_fingerprint, target_config


def terraform_json(plan):
    return json.loads(subprocess.check_output(["terraform", "show", "-json", plan], text=True))


def guard_plan(plan, target):
    if plan.get("variables", {}).get("docker_image", {}).get("value") != os.environ["TF_VAR_docker_image"]:
        raise ValueError("Saved plan uses a different image; start a new release run")
    job_image = os.environ.get("TF_VAR_dmd_cache_job_image")
    if job_image is not None and plan.get("variables", {}).get("dmd_cache_job_image", {}).get("value") != job_image:
        raise ValueError("Saved plan uses a different dm+d cache job image; start a new release run")
    destructive = [
        r
        for r in plan.get("resource_changes", [])
        if any(action in ("delete", "replace") for action in r.get("change", {}).get("actions", []))
    ]
    if destructive:
        details = "\n\n".join(
            f"{r['address']}\nactions: {json.dumps(r['change']['actions'], separators=(',', ':'))}\nreplace_paths:\n"
            + "\n".join(
                "  - " + json.dumps(path, separators=(",", ":")) for path in r["change"].get("replace_paths") or [[]]
            )
            for r in destructive
        )
        adopted = target.get("require_existing_state")
        heading = "Migration Guard Failure: Destructive Plan Detected" if adopted else "Destructive Plan Warning"
        if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary_path, "a") as summary:
                summary.write(f"### {heading}\n```text\n{details}\n```\n")
        if adopted:
            raise ValueError("Migration guard rejected destructive actions (delete/replace):\n" + details)
        print("::warning::The plan contains destructive actions (delete/replace)")
        print(details)
    return [
        f"{', '.join(r['change']['actions'])} {r['address']}"
        for r in plan.get("resource_changes", [])
        if r.get("change", {}).get("actions") not in (["no-op"], ["read"])
    ]


def expected_manifest(target, store):
    return {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "source_commit_sha": os.environ["GITHUB_SHA"],
        "workflow_run_id": os.environ["GITHUB_RUN_ID"],
        "target_id": target["id"],
        "azure_tenant_id": os.environ["ARM_TENANT_ID"],
        "azure_subscription_id": os.environ["ARM_SUBSCRIPTION_ID"],
        "backend_file": target["backend_file"],
        "backend_resource_group": store.coords["resource_group_name"],
        "backend_storage_account": store.coords["storage_account_name"],
        "backend_container": store.coords["container_name"],
        "backend_key": store.coords["key"],
        "candidate_image_digest": os.environ["IMAGE_DIGEST"],
        "infra_input_hash": infra_fingerprint(target),
        "plan_blob_path": f"{target['id']}-{os.environ['GITHUB_SHA']}-{os.environ['GITHUB_RUN_ID']}.tfplan",
    }


def verify_manifest(manifest, expected):
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Original plan manifest mismatch: {key}")
    value = manifest.get("plan_sha256", "")
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Invalid original plan checksum")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "download"])
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    target = target_config(args.target)
    store = BlobStore(target)
    expected = expected_manifest(target, store)
    os.chdir(ROOT / "infra")
    name = expected["plan_blob_path"]
    manifest_name = name + ".manifest.json"
    has_plan = store.exists(name, "tfplans")
    has_manifest = store.exists(manifest_name, "tfplans")
    if has_plan != has_manifest:
        raise ValueError("Incomplete saved plan/manifest pair; use a new workflow run after investigating publication")
    if has_plan:
        manifest = store.read(manifest_name, "tfplans")
        verify_manifest(manifest, expected)
        store.command("download", name, "tfplans", file=str(ROOT / "infra" / name), overwrite="true")
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != manifest["plan_sha256"]:
            raise ValueError("Downloaded plan differs from its original stored checksum")
        print("Reusing original saved plan and manifest")
    elif args.action == "download":
        raise ValueError("Reviewed plan is missing; refusing to generate a replacement during apply")
    else:
        with open("plan_output.json", "w") as log:
            result = subprocess.run(
                [
                    "terraform",
                    "plan",
                    "-input=false",
                    "-lock-timeout=120s",
                    f"-var-file=../{target['tfvars_file']}",
                    f"-out={name}",
                    "-json",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        if result.returncode:
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/deploy/filter_tf_error.py"),
                    str(result.returncode),
                    "plan_output.json",
                ],
                check=True,
            )
            raise RuntimeError("Terraform plan failed; no plan was published")
        manifest = dict(expected, plan_sha256=hashlib.sha256(Path(name).read_bytes()).hexdigest())
    # Validate both new and downloaded plans before publishing or applying.
    changes = guard_plan(terraform_json(name), target)
    Path("manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    Path("manifest.json").chmod(0o600)
    if not has_plan:
        store.command("upload", name, "tfplans", file=str(ROOT / "infra" / name), overwrite="false")
        store.command("upload", manifest_name, "tfplans", file=str(ROOT / "infra/manifest.json"), overwrite="false")
    with open(os.environ["GITHUB_ENV"], "a") as output:
        output.write(f"PLAN_FILE={name}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"### Infrastructure: {target['id']}\n")
        summary.write(f"Plan SHA256: `{manifest['plan_sha256']}`\n\n")
        summary.write(f"Image: `{manifest['candidate_image_digest']}`\n\n")
        summary.write("```text\n" + ("\n".join(changes) or "No changes.") + "\n```\n")


if __name__ == "__main__":
    main()
