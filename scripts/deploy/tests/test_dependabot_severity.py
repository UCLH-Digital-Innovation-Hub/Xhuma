"""Exercise the actual workflow script with mocked GitHub HTTP responses."""

import base64
import json
import os
import tempfile
import tomllib
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.load((ROOT / ".github/workflows/dependabot-severity.yml").read_text(), Loader=yaml.BaseLoader)
SCRIPT = WORKFLOW["jobs"]["label"]["steps"][0]["run"]
COMPILED = compile(SCRIPT, str(ROOT / ".github/workflows/dependabot-severity.yml"), "exec")
PYPI = "https://pypi.org/simple"
LEGACY_PYPI = "https://pypi.python.org/simple"


def lock(packages):
    return "\n".join(
        f"[[package]]\nname = {json.dumps(name)}\nversion = {json.dumps(version)}\n"
        f"source = {{registry = {json.dumps(registry)}}}\n"
        for name, version, registry in packages
    )


def advisory(identifier="GHSA-test-0000-0001", severity="high"):
    return {"ghsa_id": identifier, "severity": severity}


class Response:
    def __init__(self, data, headers=None):
        self.data = data
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"" if self.data is None else json.dumps(self.data).encode()


class FakeGitHub:
    def __init__(
        self, before, after, advisories=None, labels=(), state="open", latest=None, pages=None, label_error=None
    ):
        self.before = before
        self.after = after
        self.advisories = advisories or {}
        self.labels = set(labels)
        self.existing = set(labels)
        self.pr = {"state": state, "base": {"sha": "base-sha"}, "head": {"sha": "head-sha"}}
        self.latest = latest
        self.pages = pages
        self.label_error = label_error
        self.pr_reads = 0
        self.calls = []
        self.namespace = {}

    def urlopen(self, req, timeout):
        parsed = urllib.parse.urlsplit(req.full_url)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        method = req.get_method()
        data = None if req.data is None else json.loads(req.data)
        self.calls.append((method, path, query, data))
        assert timeout == 60
        assert parsed.netloc == "api.github.test"
        assert req.get_header("Authorization") == "Bearer fake-test-token"
        if path == "/repos/org/repo/pulls/42":
            self.pr_reads += 1
            return Response(self.pr if self.pr_reads == 1 or self.latest is None else self.latest)
        if path == "/repos/org/repo/contents/uv.lock":
            content = self.before if query["ref"] == ["base-sha"] else self.after
            return Response({"encoding": "base64", "content": base64.b64encode(content.encode()).decode()})
        if path == "/advisories":
            assert query["ecosystem"] == ["pip"]
            assert query["type"] == ["reviewed"]
            assert query["is_withdrawn"] == ["false"]
            assert query["per_page"] == ["100"]
            assert set(query) <= {"ecosystem", "affects", "type", "is_withdrawn", "per_page", "after", "before"}, (
                "Global advisories pagination does not support page; use the Link-header cursor"
            )
            if self.pages is not None and query["affects"] == ["demo@1"]:
                # The documented API uses Link headers and before/after cursors.
                if query.get("after") == ["next+cursor/=="]:
                    return Response(self.pages[1])
                return Response(
                    self.pages[0],
                    {"Link": '<https://api.github.test/advisories?after=next%2Bcursor%2F%3D%3D>; rel="next"'},
                )
            return Response(self.advisories.get(query["affects"][0], []))
        if path.startswith("/repos/org/repo/labels/"):
            label = urllib.parse.unquote(path.rsplit("/", 1)[1])
            if self.label_error or label not in self.existing:
                code = self.label_error or 404
                raise urllib.error.HTTPError(req.full_url, code, "mock error", {}, None)
            return Response({"name": label})
        if path == "/repos/org/repo/labels" and method == "POST":
            assert len(data["color"]) == 6
            self.existing.add(data["name"])
            return Response(data)
        if path == "/repos/org/repo/issues/42":
            return Response({"labels": [{"name": label} for label in sorted(self.labels)]})
        if path == "/repos/org/repo/issues/42/labels" and method == "POST":
            assert set(data["labels"]) <= self.existing
            self.labels.update(data["labels"])
            return Response(None)
        if path.startswith("/repos/org/repo/issues/42/labels/") and method == "DELETE":
            self.labels.remove(urllib.parse.unquote(path.rsplit("/", 1)[1]))
            return Response(None)
        raise AssertionError(f"Unexpected API operation: {method} {path} {query}")

    def run(self):
        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp) / "event.json"
            summary = Path(temp) / "summary.md"
            event.write_text(json.dumps({"pull_request": {"number": 42}}))
            env = {
                "GITHUB_API_URL": "https://api.github.test",
                "GITHUB_REPOSITORY": "org/repo",
                "GH_TOKEN": "fake-test-token",
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_STEP_SUMMARY": str(summary),
                "LOCKFILE": "uv.lock",
            }
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", self.urlopen):
                exec(COMPILED, self.namespace)
            return summary.read_text() if summary.exists() else ""

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


class GitHubFilesTests(unittest.TestCase):
    def example(self, registry=PYPI, advisories=None, **kwargs):
        return FakeGitHub(lock([("demo", "1", registry)]), lock([("demo", "2", registry)]), advisories, **kwargs)

    def test_dependabot_yaml_and_paths(self):
        config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
        self.assertEqual(config["version"], 2)
        for update in config["updates"]:
            directory = ROOT / update["directory"].lstrip("/")
            self.assertTrue((directory / "pyproject.toml").is_file())
            self.assertTrue((directory / "uv.lock").is_file())
            self.assertEqual(update["package-ecosystem"], "uv")

    def test_workflow_trigger_and_security_boundary(self):
        trigger = WORKFLOW["on"]["pull_request_target"]
        self.assertEqual(trigger["branches"], ["dev"])
        self.assertEqual(set(trigger["types"]), {"opened", "synchronize", "reopened"})
        job = WORKFLOW["jobs"]["label"]
        self.assertIn("github.event.pull_request.user.login == 'dependabot[bot]'", job["if"])
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", job["if"])
        self.assertFalse(any("uses" in step for step in job["steps"]))
        self.assertEqual(WORKFLOW["permissions"], {"contents": "read", "pull-requests": "write", "issues": "write"})
        self.assertEqual(WORKFLOW["concurrency"]["cancel-in-progress"], "false")

    def test_highest_fixed_severity_and_unrelated_labels_preserved(self):
        fake = self.example(
            advisories={"demo@1": [advisory(severity="low"), advisory("GHSA-test-0000-0002", "critical")]},
            labels=["dependencies", "python", "severity:low"],
        )
        summary = fake.run()
        self.assertEqual(fake.labels, {"dependencies", "python", "security", "severity:critical"})
        self.assertIn("GHSA-test-0000-0002", summary)

    def test_severity_mapping(self):
        for severity, expected in [
            ("medium", "moderate"),
            ("moderate", "moderate"),
            ("high", "high"),
            ("low", "low"),
            ("critical", "critical"),
            ("unexpected", "unknown"),
            (None, "unknown"),
        ]:
            with self.subTest(severity=severity):
                fake = self.example(advisories={"demo@1": [advisory(severity=severity)]})
                fake.run()
                self.assertEqual(fake.labels, {"security", f"severity:{expected}"})

    def test_missing_severity(self):
        fake = self.example(advisories={"demo@1": [{"ghsa_id": "GHSA-test-0000-0001"}]})
        fake.run()
        self.assertEqual(fake.labels, {"security", "severity:unknown"})

    def test_no_fix_removes_stale_managed_labels(self):
        fake = self.example(labels=["dependencies", "python", "security", "severity:high", "custom"])
        summary = fake.run()
        self.assertEqual(fake.labels, {"dependencies", "python", "custom"})
        self.assertIn("No fixes matched", summary)

    def test_remaining_vulnerability_is_not_a_fix(self):
        item = advisory()
        fake = self.example(advisories={"demo@1": [item], "demo@2": [item]})
        fake.run()
        self.assertEqual(fake.labels, set())

    def test_new_vulnerability_is_not_a_fix(self):
        fake = self.example(advisories={"demo@2": [advisory()]})
        fake.run()
        self.assertEqual(fake.labels, set())

    def test_unchanged_packages_avoid_advisory_queries(self):
        fake = FakeGitHub(lock([("demo", "1", PYPI)]), lock([("demo", "1", PYPI)]))
        fake.run()
        self.assertFalse(any(call[1] == "/advisories" for call in fake.calls))

    def test_fix_must_be_absent_from_all_new_versions(self):
        fake = FakeGitHub(
            lock([("demo", "1", PYPI)]),
            lock([("demo", "2", PYPI), ("demo", "3", PYPI)]),
            {"demo@1": [advisory()], "demo@3": [advisory()]},
        )
        fake.run()
        self.assertEqual(fake.labels, set())

    def test_fix_when_all_new_versions_are_safe(self):
        fake = FakeGitHub(
            lock([("demo", "1", PYPI)]), lock([("demo", "2", PYPI), ("demo", "3", PYPI)]), {"demo@1": [advisory()]}
        )
        fake.run()
        self.assertEqual(fake.labels, {"security", "severity:high"})

    def test_names_are_normalized(self):
        fake = FakeGitHub(
            lock([("Demo_Package.Name", "1", PYPI)]),
            lock([("demo-package-name", "2", PYPI)]),
            {"demo-package-name@1": [advisory()]},
        )
        fake.run()
        self.assertEqual(fake.labels, {"security", "severity:high"})

    def test_private_registry_is_excluded(self):
        fake = self.example(registry="https://private.example/simple", advisories={"demo@1": [advisory()]})
        fake.run()
        self.assertFalse(any(call[1] == "/advisories" for call in fake.calls))

    def test_repository_lockfile_packages_are_scanned(self):
        actual = tomllib.loads((ROOT / "uv.lock").read_text())
        public = [
            p for p in actual["package"] if p.get("source", {}).get("registry", "").rstrip("/") in {PYPI, LEGACY_PYPI}
        ]
        self.assertTrue(public, "Regression fixture must match actual repository registry")
        fake = FakeGitHub((ROOT / "uv.lock").read_text(), (ROOT / "uv.lock").read_text())
        fake.run()
        self.assertEqual(
            len(fake.namespace["before"]),
            len({p["name"] for p in public}),
            "Workflow must recognize public PyPI packages in the repository lockfile",
        )

    def test_real_registry_security_fix_is_labeled(self):
        fake = self.example(registry=LEGACY_PYPI, advisories={"demo@1": [advisory()]}, labels=["dependencies"])
        fake.run()
        self.assertEqual(fake.labels, {"dependencies", "security", "severity:high"})

    def test_cursor_pagination(self):
        first_page = [advisory(f"GHSA-test-0000-{i:04}", "low") for i in range(100)]
        fake = self.example(pages=[first_page, [advisory("GHSA-test-last-page", "critical")]])
        fake.run()
        self.assertEqual(fake.labels, {"security", "severity:critical"})

    def test_short_page_with_next_link_is_followed(self):
        fake = self.example(pages=[[advisory(severity="low")], [advisory("GHSA-test-last-page", "critical")]])
        fake.run()
        self.assertEqual(fake.labels, {"security", "severity:critical"})

    def test_full_page_without_next_link_is_final(self):
        items = [advisory(f"GHSA-test-0000-{i:04}") for i in range(100)]
        fake = self.example(advisories={"demo@1": items})
        fake.run()
        self.assertEqual(fake.labels, {"security", "severity:high"})
        self.assertEqual(sum(call[1] == "/advisories" for call in fake.calls), 2)

    def test_invalid_or_repeated_cursor_aborts_without_mutation(self):
        for link in [
            '<https://api.github.test/advisories?after=next%2Bcursor%2F%3D%3D>; rel="next"',
            '<https://api.github.test/advisories?before=unexpected>; rel="next"',
        ]:
            with self.subTest(link=link):
                fake = self.example(pages=[[advisory()], []])
                original = fake.urlopen

                def response_with_link(req, timeout):
                    response = original(req, timeout)
                    if "/advisories?" in req.full_url:
                        response.headers["Link"] = link
                    return response

                fake.urlopen = response_with_link
                with self.assertRaisesRegex(RuntimeError, "pagination cursor"):
                    fake.run()
                self.assertFalse(fake.writes())

    def test_trailing_slash_on_public_registry(self):
        for registry in [PYPI, LEGACY_PYPI]:
            with self.subTest(registry=registry):
                fake = self.example(registry=registry + "/", advisories={"demo@1": [advisory()]})
                fake.run()
                self.assertEqual(fake.labels, {"security", "severity:high"})

    def test_closed_pr_exits_without_mutation(self):
        fake = self.example(state="closed")
        with self.assertRaises(SystemExit) as raised:
            fake.run()
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(fake.writes())

    def test_changed_pr_exits_without_mutation(self):
        for changed in ["head", "base", "closed"]:
            with self.subTest(changed=changed):
                latest = {"state": "open", "base": {"sha": "base-sha"}, "head": {"sha": "head-sha"}}
                if changed == "closed":
                    latest["state"] = "closed"
                else:
                    latest[changed]["sha"] = "changed-sha"
                fake = self.example(advisories={"demo@1": [advisory()]}, latest=latest)
                with self.assertRaisesRegex(RuntimeError, "PR changed"):
                    fake.run()
                self.assertFalse(fake.writes())

    def test_existing_labels_are_not_recreated(self):
        fake = self.example(advisories={"demo@1": [advisory()]}, labels=["security", "severity:high"])
        fake.run()
        self.assertFalse(any(call[1] == "/repos/org/repo/labels" and call[0] == "POST" for call in fake.calls))

    def test_non_404_label_error_aborts_without_mutation(self):
        fake = self.example(advisories={"demo@1": [advisory()]}, label_error=403)
        with self.assertRaises(urllib.error.HTTPError):
            fake.run()
        self.assertFalse(fake.writes())

    def test_added_or_removed_packages_do_not_claim_version_fixes(self):
        fake = FakeGitHub(lock([("removed", "1", PYPI)]), lock([("added", "1", PYPI)]), {"removed@1": [advisory()]})
        fake.run()
        self.assertEqual(fake.labels, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
