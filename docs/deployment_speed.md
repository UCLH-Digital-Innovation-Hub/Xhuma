# Deployment routing, caching and retry behaviour

This change is based on dev commit `03c981ef644d4851d72567ba749e89dd91a56933`.
It preserves the existing dev → play, int → int, main → prd selection rules.
Every target selected for a release receives the same immutable application digest.
It does not deploy the same version to different branch environments simultaneously.

## Files and responsibilities

| File | Responsibility |
| --- | --- |
| `.github/workflows/matrix-deploy.yml` | Run CI and image resolution/build concurrently, scan once, pass one digest into the target matrix. |
| `.github/workflows/deploy-target.yml` | Detect, optionally plan/apply infrastructure, then deploy one target independently. |
| `scripts/deploy/deployment_state.py` | Fingerprint target inputs; persist infrastructure success and apply receipts. |
| `scripts/deploy/release_image.py` | Reuse an image matching build inputs; preserve a run's original digest on retry. |
| `scripts/deploy/saved_plan.py` | Validate, publish, download and verify immutable plans and their original manifests. |
| `scripts/deploy/tests/test_deployment_speed.py` | Exercise changes, storage failures and retry cases using local git and mocked cloud operations. |
| `scripts/deploy/tests/test_workflow_wiring.py` | Adapt existing checks to the reusable target workflow. |

## Application image selection

Tracked application/build inputs are fingerprinted once. GHCR tags are:

- `input-<fingerprint>`: locate an image built from identical application inputs.
- `run-<workflow-run-id>`: preserve an image built by that run.

The release artifact `release-image` stores the selected digest, source commit,
run ID, repository and fingerprint. On retry it takes precedence over tags.
A reused image is scanned again before any rollout. Tests and image preparation
run concurrently, but rollout requires both to succeed. An image build can
therefore be published even if tests subsequently fail; that run cannot deploy it.

The fingerprint includes the Dockerfile, uv lockfile, project metadata, startup
script, app source, migrations and image-building workflow/helper. Identical
contents can reuse an image across different commit histories. Current Dockerfile
COPY instructions include app tests, so changes to those tests also change the image.
If COPY inputs, build arguments or platforms change, update the fingerprint inputs too.

## Infrastructure selection

For each target, fingerprint the common Terraform root, common shared root,
that target's tfvars/backend files and selected shared backend, bootstrap/deploy
scripts and deployment workflows. The target configuration is included too.
Changes to the central targets inventory conservatively trigger all selected targets.

Compare that fingerprint with the last successfully reconciled fingerprint,
not just the preceding commit. An unapplied change remains pending across later
application commits. Missing records require infrastructure work; authentication,
storage and identity errors stop the workflow rather than masquerading as cache misses.

The success record includes the source SHA and run identity for audit, but the
fingerprint decides whether tracked inputs changed. These records are stored at
`deployments/<target>/infra-success.json` inside the target's existing **tfstate**
container. They are not stored in the seven-day tfplans lifecycle scope.

The first release using this change runs infrastructure for each selected target
because no success record exists yet. No resources or Terraform state are moved.
Existing-state preflight and resource-anchor guards remain in place.

## Plan reuse and interrupted applies

Plans remain `<target>-<commit>-<run-id>.tfplan` in the existing tfplans container.
Their original manifests are stored alongside them with `.manifest.json` appended.
Both paths validate target/backend identity, commit, run, image digest, tracked
infrastructure inputs and the original checksum. Destructive changes are checked
before publication and again on reuse. A hash is never silently reissued for a
changed existing plan. An incomplete pair stops with a request for a new run.

After Terraform apply succeeds, write an immutable per-run workload receipt.
If shared reconciliation or verification then fails, retry uses the receipt to
avoid applying that completed workload plan again, and resumes the guarded shared
reconciliation. Write the overall infrastructure success record only after all
infrastructure verification succeeds. If the application deployment later fails,
rerunning all jobs with `force_infra` and `bootstrap_state` off skips the completed
infrastructure; rerunning failed jobs can reuse their successful dependencies as usual.

A partial/failed Terraform apply can leave the saved plan stale. Do not silently
replan under the old approval: start a new workflow run and review its fresh plan.
The same applies if Azure apply succeeded but its receipt could not be saved.
A run older than a recorded successful infrastructure run cannot overwrite it;
perform deliberate rollbacks through a new run. Plan/state freshness cannot detect
all direct Azure changes.

Plans are retained by the existing seven-day lifecycle policy; retries requiring
an expired plan need a new run. Keep the release image artifact through your retry
window and retain referenced GHCR digests. Pre-change orphan plans without original
manifests are not automatically adopted.

## Caches and concurrency

Provider binaries are cached by OS, architecture and both Terraform lockfiles.
Only the provider cache is restored: backend metadata, state and plans are not
GitHub cache entries. Workload and shared init calls in the apply job run
sequentially and share its local provider cache. Separate jobs have separate
local caches. GitHub's branch cache visibility rules still apply; a cache miss is safe.
Docker layer caching is disabled. Each actual build pulls current base images
and runs without cached layers; unchanged application inputs can still reuse
the existing scanned application image without building it again.

Existing branch-level workflow serialization is retained to avoid allowing older
approved releases to overtake one another. Each target has its own reusable job
chain, removing cross-target plan/apply barriers. Apply jobs sharing the same
shared-backend filename are serialized to protect live-ACL read/modify/write work.
This conservatively also serializes their workload apply steps. Active deployments
and applies are not canceled automatically.

## Manual inputs and checks outside git

All three inputs default to `false`. Leave them off for ordinary releases: the workflow
already builds when it cannot reuse an image and runs Terraform when infrastructure
inputs changed or no success record exists. Push-triggered runs use these defaults.
Set overrides when starting a new manual **Matrix Deployment** run.

| Input | When to enable it | What it does |
| --- | --- | --- |
| `force_rebuild` | Pick up refreshed Docker base images or downloaded packages when tracked application files are unchanged, or deliberately rebuild the same source. | Bypasses reuse of an image with matching inputs in a new run. The build pulls base images and disables Docker layer caching. Infrastructure still follows its own change detection. |
| `force_infra` | Reconcile Azure drift, or apply changes to GitHub environment secrets/variables consumed by Terraform, such as `POSTGRES_PASSWORD` or registry credentials. | Runs infrastructure planning, apply and shared-vault verification even when the tracked fingerprint matches. The application image can still be reused. Review the generated plan before approving apply. |
| `bootstrap_state` | Create/update the target's backend storage or reapply its versioning, retention and plan-expiry policy. | Runs `infra/bootstrap/setup-target.sh` before detection and also enables `force_infra`. The resource group must already exist. |

Enable both `force_rebuild` and `force_infra` when you need a fresh build and explicit
infrastructure reconciliation. These are full releases: CI, image scanning, target
selection, environment approvals and application deployment still apply.

`bootstrap_state` does not create/import missing Terraform state or initialize the
separate shared-vault backend. All current targets require existing state and resource
anchors, so an empty backend still fails those checks. None of these inputs populates
Key Vault secret values; vault population remains an operator task.

### Run from GitHub

Use the repository's [manual workflow controls](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow):

1. Open **Actions → Matrix Deployment → Run workflow**.
2. Select the source branch: `dev` deploys to **play**, `int` to **int**, and `main`
   to **prd**. The flags do not override this branch-to-target mapping.
3. Enable the inputs needed for your task; leave the others unchecked.
4. Click **Run workflow**. Review any infrastructure plan and complete the configured
   environment approvals as the run progresses.

### Run from the command line

From this repository, with an authenticated [GitHub CLI](https://cli.github.com/manual/gh_workflow_run),
use one of these examples. Each command starts a new run; omitted inputs default to `false`.

```bash
# Fresh application build for play; leave infrastructure to normal change detection.
gh workflow run matrix-deploy.yml --ref dev -f force_rebuild=true

# Reconcile INT after a Terraform input secret changed or to apply Azure drift.
gh workflow run matrix-deploy.yml --ref int -f force_infra=true

# Request both a fresh build and infrastructure reconciliation for play.
gh workflow run matrix-deploy.yml --ref dev -f force_rebuild=true -f force_infra=true

# Maintain play's backend storage and policies; also force infrastructure reconciliation.
gh workflow run matrix-deploy.yml --ref dev -f bootstrap_state=true
```

### New runs versus retries

- To request another fresh build, start a **new run** with `force_rebuild=true`.
  Rerunning an existing release preserves its original published image through the
  release artifact or run tag, even if that run already has `force_rebuild=true`.
  A fresh build may produce the same digest; deploying an already-configured digest
  skips the container update and still verifies configuration and health.
- To change input choices, submit a new **Run workflow** request. A rerun retains
  the original flags. When rerunning all jobs with `force_infra=true` or
  `bootstrap_state=true`, infrastructure is requested again; a successful workload
  receipt can skip the completed apply, while shared-vault reconciliation and
  verification run again. Retrying only failed application jobs can reuse the
  completed infrastructure dependencies.
- After changing Terraform input secrets, or when a saved plan is stale, expired
  or left by a partial apply, start a new run with `force_infra=true` and review its
  fresh plan. A retry reuses the original plan; these flags do not replace it under
  an existing approval or bypass state, identity and destructive-change guards.

No new secrets or Azure roles are introduced; storage access uses the account-key
permission already required by the existing workflow. The build job needs
`packages: write` and `actions: read` for image reuse and release artifacts.
Environment approvals and secrets stay on the existing target environments.
If a `<target>-plan` environment requires approval, the detection job still waits
for that approval, even when it ultimately skips Terraform.

This change does not add a scheduled drift workflow. The existing application
health check and its blocked-network/manual-acceptance behaviour are preserved;
a successful job does not prove the new container is ready for clinical traffic.

## Local validation and rollout

```bash
python -m pytest -c /dev/null scripts/deploy/tests -q
ruff check scripts/deploy
ruff format --check scripts/deploy
actionlint -shellcheck= .github/workflows/matrix-deploy.yml .github/workflows/deploy-target.yml
```

The focused tests need pytest, PyYAML, jsonschema and git. They do not connect to
Azure/GHCR or apply infrastructure. Live GitHub environment-secret resolution,
GHCR token access and Azure checkpoint/plan publication still require a rehearsal
using play before promotion. The existing pinned Terraform version is unchanged.
