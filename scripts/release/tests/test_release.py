import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bump_version as version  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def files():
    return {
        "pyproject.toml": '[project]\nname = "Xhuma"\nversion = "1.0.3" # canonical\n',
        "infra/variables.tf": 'variable "app_version" {\n  type = string\n  default = "1.0.3"\n}\n',
        **{f"infra/env/{env}.tfvars": 'app_version    = "1.0.3" # release\n' for env in ["play", "int", "prd"]},
        "uv.lock": 'version = 1\n[[package]]\nname = "xhuma"\nversion = "1.0.3"\nsource = { virtual = "." }\n'
        '\n[[package]]\nname = "unrelated"\nversion = "1.0.4"\n',
    }


def test_aligned(files):
    assert version.inspect(files)[0] == "1.0.3"


def test_patch_preserves_every_other_byte(files):
    updated = version.synchronise(files, version.patch("1.0.3"))
    assert version.inspect(updated)[0] == "1.0.4"
    for path in files:
        assert updated[path] == files[path].replace('"1.0.3"', '"1.0.4"')
    assert version.patch("1.2.99") == "1.2.100"


@pytest.mark.parametrize("path", version.FILES)
def test_drift(files, path):
    files[path] = files[path].replace('"1.0.3"', '"1.0.9"')
    with pytest.raises(ValueError, match="drift"):
        version.synchronise(files, "1.0.4")


@pytest.mark.parametrize("value", ["1.2", "01.2.3", "1.2.3-rc1", "1.2.3+build", "v1.2.3", "-1.2.3", "garbage"])
def test_malformed(files, value):
    files["pyproject.toml"] = files["pyproject.toml"].replace('"1.0.3"', f'"{value}"')
    with pytest.raises(ValueError, match="Unsupported"):
        version.inspect(files)


@pytest.mark.parametrize("path", version.FILES)
def test_missing_and_duplicate_declarations(files, path):
    _, declarations = version.inspect(files)
    start, _ = declarations[path][1]
    line_start = files[path].rfind("\n", 0, start) + 1
    line_end = files[path].find("\n", start) + 1
    line = files[path][line_start:line_end]
    for replacement in ["", line + line]:
        broken = {**files, path: files[path][:line_start] + replacement + files[path][line_end:]}
        with pytest.raises(ValueError):
            version.inspect(broken)


def test_check_idempotent_and_failed_bump_writes_nothing(files, tmp_path, monkeypatch):
    for path, content in files.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content.replace("\n", "\r\n").encode())
    monkeypatch.setattr(version, "ROOT", tmp_path)
    before = {path: (tmp_path / path).read_bytes() for path in files}
    monkeypatch.setattr(sys, "argv", ["bump_version.py", "--check"])
    assert version.main() == version.main() == 0
    assert before == {path: (tmp_path / path).read_bytes() for path in files}
    monkeypatch.setattr(sys, "argv", ["bump_version.py", "patch"])
    assert version.main() == 0
    assert version.inspect({path: (tmp_path / path).read_bytes().decode() for path in files})[0] == "1.0.4"
    (tmp_path / "infra/env/int.tfvars").write_text('app_version = "1.0.7"\n')
    before = {path: (tmp_path / path).read_bytes() for path in files}
    assert version.main() == 1
    assert before == {path: (tmp_path / path).read_bytes() for path in files}


@pytest.mark.parametrize("text", ['nested = {\napp_version = "1.0.3"\n}\n', 'app_version = "1.0.3" + "x"\n'])
def test_ambiguous_hcl_fails(files, text):
    files["infra/env/int.tfvars"] = text
    with pytest.raises(ValueError):
        version.inspect(files)


def test_ci_checks_application_versions():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert any(
        step.get("run") == "python scripts/release/bump_version.py --check" for step in ci["jobs"]["quality"]["steps"]
    )
