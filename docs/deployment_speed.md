# Concurrent deployment gates and resumable retries

Deployments preserve the existing branch routing: `dev` → Play, `int` → INT,
`main` → PRD. Each new workflow run builds and scans the application image and
runs Terraform for every selected target.

## Workflow

`.github/workflows/matrix-deploy.yml` starts CI, build/scan and target selection
concurrently. Rollout waits for all three to succeed. A build may publish an
image before CI finishes, but deployment cannot proceed if any gate fails.

The build uses the existing commit-SHA tags and passes its digest to deployment,
including the dm+d Container Apps Job. There are no application-input hashes,
image lookup/reuse records, conditional builds, infrastructure success hashes or
`force_rebuild` / `force_infra` controls.

Each target calls `.github/workflows/deploy-target.yml` independently:

1. Bootstrap backend storage, check existing state and anchors where required,
   initialise Terraform, and create or verify the run's saved plan.
2. Pass the configured infrastructure approval gate, verify the plan and original
   manifest, apply workload infrastructure, and record a successful apply receipt.
3. Extract current Terraform outputs, reconcile shared Key Vault rules for the
   App Service and optional Container Apps subnets, and verify the job image and
   App Service Key Vault references.
4. Pass the configured application approval gate, deploy the scanned build digest,
   verify image configuration, and report the application health check.

Terraform runs for every new release, even when only application code changed.
Shared reconciliation is serialised by shared backend to protect its live
read/merge/apply sequence. Existing Terraform locks and destructive-change guards
remain in place.

## Retry a failed deployment

Use GitHub Actions **Re-run failed jobs** to retain the successful build's output
and retry unfinished deployment work. Each target has its own workflow chain, so
failed targets can resume without requiring successful targets to deploy again.

`scripts/deploy/saved_plan.py` restores the original plan and manifest from the
target's `tfplans` container. Before use it validates the repository, commit,
workflow run, target, Azure identity, backend, image digest, tracked infrastructure
inputs and plan SHA-256. The saved plan is never silently replaced. Missing,
corrupt, mismatched or incomplete plan/manifest pairs stop the retry.

`scripts/deploy/deployment_state.py` stores a run-specific workload apply receipt
at `deployments/<target>/runs/<run-id>/workload-applied.json` in the target's state
container. It is written only after a successful workload apply and binds the
receipt to the original plan and image. If workload apply succeeded but shared
reconciliation or verification failed, the retry skips that completed apply and
continues with output extraction, shared reconciliation and verification.

A partial or failed workload apply has no success receipt. If Terraform rejects
its saved plan as stale, start a new workflow run and review the fresh plan. The
same applies if saving the receipt failed after Terraform completed, or the saved
plan expired (backend policy retains plans for seven days).

**Re-run all jobs** also reruns the build. If the rebuilt image digest differs,
the original saved-plan validation deliberately rejects it; start a new workflow
run to build, plan and approve that candidate together. There is no image record
or run tag to force reuse of an earlier build.

Retry only the current release. Start a new workflow run for a rollback or after
a newer release has deployed; per-branch concurrency prevents overlapping runs,
but does not stop an operator rerunning an older run.

## Local checks

```bash
uv run --locked python -m pytest scripts/deploy/tests
uv run --locked ruff check .
uv run --locked ruff format --check .
```

These checks exercise workflow gates, target routing, plan integrity, apply
receipts and safe migration diagnostics without accessing Azure or GHCR. Live
Azure application and shared-vault verification still run during deployment.
