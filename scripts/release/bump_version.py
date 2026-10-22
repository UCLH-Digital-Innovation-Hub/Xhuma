"""Strict, formatting-preserving application version maintenance (Python 3.11+)."""

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "pyproject.toml",
    "infra/variables.tf",
    "infra/env/play.tfvars",
    "infra/env/int.tfvars",
    "infra/env/prd.tfvars",
    "uv.lock",  # Derived project metadata required by uv sync --locked.
)
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def patch(version):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError(f"Unsupported application version: {version!r}; expected MAJOR.MINOR.PATCH")
    major, minor, number = version.split(".")
    return f"{major}.{minor}.{int(number) + 1}"


def one(items, description):
    if len(items) != 1:
        raise ValueError(f"Expected exactly one {description}; found {len(items)}")
    return items[0]


def toml_span(path, text):
    data = tomllib.loads(text)
    if path == "pyproject.toml":
        version = data.get("project", {}).get("version")
        section = one(list(re.finditer(r"(?m)^\[project\][ \t]*(?:#.*)?\r?$", text)), "[project] section")
        start = section.end()
        end = re.search(r"(?m)^\[", text[start:])
        end = start + end.start() if end else len(text)
    else:
        package = one([p for p in data.get("package", []) if p.get("name") == "xhuma"], "xhuma lock package")
        if package.get("source") != {"virtual": "."}:
            raise ValueError("Unexpected Xhuma lockfile source")
        version = package.get("version")
        blocks = list(re.finditer(r"(?ms)^\[\[package\]\]\r?\n.*?(?=^\[\[package\]\]|\Z)", text))
        block = one([b for b in blocks if re.search(r'^name = "xhuma"\r?$', b[0], re.M)], "Xhuma lockfile block")
        start, end = block.span()
    patch(version)
    match = one(
        list(re.finditer(r'(?m)^[ \t]*version[ \t]*=[ \t]*["\']([^"\'\r\n]*)["\'][ \t]*(?:#.*)?\r?$', text[start:end])),
        f"{path} version declaration",
    )
    if match[1] != version:
        raise ValueError(f"Ambiguous version declaration in {path}")
    return version, (start + match.start(1), start + match.end(1))


def hcl_span(path, text):
    # Tokenise strings and comments so commented declarations and braces in strings
    # cannot be mistaken for live HCL. Reject constructs outside this small grammar.
    pattern = re.compile(r'\s+|#[^\n]*|//[^\n]*|/\*.*?\*/|"(?:[^"\\]|\\.)*"|[A-Za-z_][\w-]*|[^\s]', re.S)
    tokens = [m for m in pattern.finditer(text) if not m[0].isspace() and not m[0].startswith(("#", "//", "/*"))]
    if any(t[0] == '"' for t in tokens) or any(a[0] == "/" and b[0] == "*" for a, b in zip(tokens, tokens[1:])):
        raise ValueError(f"Unterminated string or comment in {path}")
    if any(a[0] == b[0] == "<" for a, b in zip(tokens, tokens[1:])):
        raise ValueError(f"Unsupported HCL heredoc in {path}")
    all_tokens = tokens
    if path.endswith("variables.tf"):
        index = one([i for i, t in enumerate(tokens) if t[0].strip('"') == "app_version"], "app_version variable label")
        if index == 0 or tokens[index - 1][0] != "variable" or tokens[index + 1][0] != "{":
            raise ValueError("Unsupported app_version variable block")
        end = next((i for i in range(index + 2, len(tokens)) if tokens[i][0] in {"{", "}"}), None)
        if end is None or tokens[end][0] != "}":
            raise ValueError("Unsupported app_version variable body")
        tokens = tokens[index + 2 : end]
        key = "default"
    else:
        key = "app_version"
    index = one([i for i, t in enumerate(tokens) if t[0].strip('"') == key], f"{path} {key} declaration")
    if tokens[index][0] != key:
        raise ValueError(f"Unsupported quoted HCL key in {path}")
    declaration_start = tokens[index].start()
    depth = sum(
        1 if t[0] in {"{", "["} else -1 if t[0] in {"}", "]"} else 0
        for t in all_tokens
        if t.start() < declaration_start
    )
    if depth != (1 if path.endswith("variables.tf") else 0):
        raise ValueError(f"Unexpected nesting of version in {path}")
    if index + 2 >= len(tokens) or tokens[index + 1][0] != "=":
        raise ValueError(f"Missing assignment in {path}")
    value = tokens[index + 2]
    if not value[0].startswith('"'):
        raise ValueError(f"Expected literal version in {path}")
    # A value must occupy its whole line, apart from whitespace and a comment.
    tail = text[value.end() :].split("\n", 1)[0].strip()
    if tail and not tail.startswith(("#", "//")):
        raise ValueError(f"Unsupported version expression in {path}")
    version = value[0][1:-1]
    patch(version)
    return version, (value.start() + 1, value.end() - 1)


def inspect(files):
    declarations = {}
    for path in FILES:
        if path not in files:
            raise ValueError(f"Missing version file: {path}")
        try:
            declarations[path] = (toml_span if path in {"pyproject.toml", "uv.lock"} else hcl_span)(path, files[path])
        except (ValueError, IndexError) as error:
            raise ValueError(f"{path}: {error}") from error
    canonical = declarations["pyproject.toml"][0]
    drift = [f"{path}={version}" for path, (version, _) in declarations.items() if version != canonical]
    if drift:
        raise ValueError(f"Version drift (canonical {canonical}): " + ", ".join(drift))
    return canonical, declarations


def synchronise(files, target):
    patch(target)
    _, declarations = inspect(files)
    result = {}
    for path, (_, (start, end)) in declarations.items():
        result[path] = files[path][:start] + target + files[path][end:]
    # Ensure textual substitutions actually changed the parsed declarations.
    if inspect(result)[0] != target:
        raise ValueError("Ambiguous version substitution")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("operation", nargs="?", choices=["patch"])
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        files = {}
        for path in FILES:
            location = ROOT / path
            if location.is_symlink():
                raise ValueError(f"Refusing symlink: {path}")
            files[path] = location.read_bytes().decode("utf-8")
        version, _ = inspect(files)
        if args.check:
            print(f"Application versions aligned: {version} (including uv.lock metadata)")
            return 0
        updated = synchronise(files, patch(version))
        for path, text in updated.items():
            (ROOT / path).write_bytes(text.encode("utf-8"))
        print(f"Xhuma version: {version} -> {patch(version)}")
        return 0
    except (ValueError, OSError) as error:
        print(f"Version check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
