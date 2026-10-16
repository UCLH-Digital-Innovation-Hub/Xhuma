"""Target-specific deployment checkpoints in the existing state container.

Provider caches and tfplans expire independently of these durable checkpoints.
Azure failures propagate: only a confirmed missing blob is a cache miss.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    return subprocess.check_output(args, text=True, cwd=ROOT).strip()


def target_config(target_id):
    targets = json.loads((ROOT / "infra/targets.json").read_text())["targets"]
    return next(t for t in targets if t["id"] == target_id)


def fingerprint(paths, extra=None):
    # Hash tracked contents, file modes and names, not commit ancestry or mtimes.
    # Missing/deleted paths disappear from ls-tree and therefore change the hash.
    tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", "HEAD", "--", *paths], cwd=ROOT)
    return hashlib.sha256(tree + json.dumps(extra, sort_keys=True).encode()).hexdigest()


def infra_fingerprint(target):
    common = [str(x.relative_to(ROOT)) for x in (ROOT / "infra").iterdir() if x.is_file()]
    shared = [str(x.relative_to(ROOT)) for x in (ROOT / "infra/shared").iterdir() if x.is_file()]
    return fingerprint(
        common
        + shared
        + [
            target["tfvars_file"],
            target["backend_file"],
            "infra/shared/" + target["shared_backend_file"],
            "infra/bootstrap",
            "scripts/deploy",
            ".github/workflows/matrix-deploy.yml",
            ".github/workflows/deploy-target.yml",
        ],
        target,
    )


def backend_coordinates(path):
    text = (ROOT / path).read_text()
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"\n]+)"', text, re.MULTILINE))


class BlobStore:
    def __init__(self, target):
        self.coords = backend_coordinates(target["backend_file"])
        self.account_key = run(
            "az",
            "storage",
            "account",
            "keys",
            "list",
            "--resource-group",
            self.coords["resource_group_name"],
            "--account-name",
            self.coords["storage_account_name"],
            "--query",
            "[0].value",
            "-o",
            "tsv",
        )
        if os.getenv("GITHUB_ACTIONS") == "true":
            print(f"::add-mask::{self.account_key}")

    def command(self, action, name, container=None, **options):
        # Put storage credentials in the child environment, not command arguments/errors.
        env = dict(os.environ, AZURE_STORAGE_KEY=self.account_key)
        args = [
            "az",
            "storage",
            "blob",
            action,
            "--account-name",
            self.coords["storage_account_name"],
            "--container-name",
            container or self.coords["container_name"],
            "--name",
            name,
            "--only-show-errors",
            "-o",
            "json",
        ]
        for key, value in options.items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        result = subprocess.run(args, env=env, text=True, capture_output=True, cwd=ROOT)
        if result.returncode:
            # Never include Azure's potentially sensitive request diagnostics in summaries.
            raise RuntimeError(f"Azure blob {action} failed for {name}; check access/storage configuration")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def exists(self, name, container=None):
        return self.command("exists", name, container)["exists"]

    def read(self, name, container=None):
        if not self.exists(name, container):
            return None
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "record.json")
            self.command("download", name, container, file=path, overwrite="true")
            return json.loads(Path(path).read_text())

    def write(self, name, record, overwrite=True):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            path.write_text(json.dumps(record, indent=2) + "\n")
            path.chmod(0o600)
            self.command("upload", name, file=str(path), overwrite=str(overwrite).lower())


def identity(target):
    return {
        "schema_version": 1,
        "repository": os.environ["GITHUB_REPOSITORY"],
        "target_id": target["id"],
        "azure_subscription_id": os.environ["ARM_SUBSCRIPTION_ID"],
        "azure_tenant_id": os.environ["ARM_TENANT_ID"],
        "backend": backend_coordinates(target["backend_file"]),
    }


def validate_record(record, expected):
    for key, value in expected.items():
        if record.get(key) != value:
            raise ValueError(f"Deployment checkpoint mismatch: {key}")


def emit(key, value):
    value = str(value).lower() if isinstance(value, bool) else str(value)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["detect", "record-infra", "workload-status", "record-workload"])
    parser.add_argument("--target", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--manifest", default="infra/manifest.json")
    args = parser.parse_args()
    target = target_config(args.target)
    store = BlobStore(target)
    expected = identity(target)
    input_hash = infra_fingerprint(target)
    marker = f"deployments/{target['id']}/infra-success.json"
    run_number = int(os.environ["GITHUB_RUN_NUMBER"])
    latest = store.read(marker)
    if latest is not None:
        validate_record(latest, expected)
        if latest.get("workflow_run_number", 0) > run_number:
            raise ValueError("A newer run has already reconciled this target; start a new run for a rollback")
    if args.action == "detect":
        record = latest
        emit("infra_needed", args.force or record is None or record.get("input_hash") != input_hash)
        emit("input_hash", input_hash)
    elif args.action == "record-infra":
        store.write(
            marker,
            dict(
                expected,
                input_hash=input_hash,
                source_commit_sha=os.environ["GITHUB_SHA"],
                workflow_run_id=os.environ["GITHUB_RUN_ID"],
                workflow_run_number=run_number,
            ),
        )
    else:
        manifest = json.loads((ROOT / args.manifest).read_text())
        receipt = dict(
            expected,
            workflow_run_id=os.environ["GITHUB_RUN_ID"],
            source_commit_sha=os.environ["GITHUB_SHA"],
            input_hash=input_hash,
            plan_sha256=manifest["plan_sha256"],
            candidate_image_digest=manifest["candidate_image_digest"],
        )
        name = f"deployments/{target['id']}/runs/{os.environ['GITHUB_RUN_ID']}/workload-applied.json"
        if args.action == "workload-status":
            record = store.read(name)
            if record is not None:
                validate_record(record, receipt)
            emit("already_applied", record is not None)
        else:
            store.write(name, receipt, overwrite=False)


if __name__ == "__main__":
    main()
