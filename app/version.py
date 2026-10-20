"""Application version from the project metadata shipped with the service."""

import tomllib
from pathlib import Path

with (Path(__file__).resolve().parent.parent / "pyproject.toml").open("rb") as project_file:
    VERSION: str = tomllib.load(project_file)["project"]["version"]
