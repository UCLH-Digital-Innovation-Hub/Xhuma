"""Resolve one GHCR digest for the whole release; never rebuild a published run tag."""

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from deployment_state import emit, fingerprint


def request_json(url, headers=None, missing_ok=False):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and missing_ok:
            return None
        raise RuntimeError(
            f"Registry/API request failed (HTTP {error.code}); refusing to treat it as a cache miss"
        ) from None


def registry_digest(image, tag):
    token = request_json(
        "https://ghcr.io/token?" + urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{image}:pull"}),
        {
            "Authorization": "Basic "
            + base64.b64encode(f"{os.environ['GITHUB_ACTOR']}:{os.environ['GH_TOKEN']}".encode()).decode()
        },
    )["token"]
    manifest = request_json(
        f"https://ghcr.io/v2/{image}/manifests/{tag}",
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, "
            "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
        },
        missing_ok=True,
    )
    if manifest is None:
        return ""
    # Request the canonical digest header rather than hash a reserialized manifest.
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{image}/manifests/{tag}",
        method="HEAD",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, "
            "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.headers["Docker-Content-Digest"]


def check_digest(digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Missing or invalid image digest")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["resolve", "record"])
    args = parser.parse_args()
    image = os.environ["IMAGE_NAME"]
    input_hash = fingerprint(
        [
            "Dockerfile",
            ".dockerignore",
            "pyproject.toml",
            "uv.lock",
            "app",
            "alembic",
            "alembic.ini",
            "startup.sh",
            ".github/workflows/matrix-deploy.yml",
            "scripts/deploy/release_image.py",
        ],
        {"platform": "linux/amd64"},
    )
    record_path = Path("release-image.json")
    expected = {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "source_commit_sha": os.environ["GITHUB_SHA"],
        "workflow_run_id": os.environ["GITHUB_RUN_ID"],
        "image_name": image,
        "input_hash": input_hash,
    }
    if args.action == "record":
        digest = os.environ["IMAGE_DIGEST"]
        check_digest(digest)
        record_path.write_text(json.dumps(dict(expected, digest=digest), indent=2) + "\n")
        emit("digest", digest)
        return
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError("Saved release image record does not match this run")
        digest = record["digest"]
    else:
        # Run tag wins even when force_rebuild remains true on a retry.
        digest = registry_digest(image, f"run-{os.environ['GITHUB_RUN_ID']}")
        if not digest and os.getenv("FORCE_REBUILD", "false") != "true":
            digest = registry_digest(image, "input-" + input_hash)
    if digest:
        check_digest(digest)
    emit("digest", digest)
    emit("input_hash", input_hash)


if __name__ == "__main__":
    main()
