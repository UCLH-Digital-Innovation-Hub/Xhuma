"""Validates operator-supplied Action Group configuration for dm+d scheduled alerting.

Fails closed when dmd_cache_schedule_enabled is true and
DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON is absent, malformed, or empty.
Safely masks IDs and exports TF_VAR_dmd_cache_alert_action_group_ids to GITHUB_ENV
without exposing resource IDs in stdout or logs.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


def is_scheduling_enabled_in_tfvars(tfvars_path: Path | str) -> bool:
    """Check if dmd_cache_schedule_enabled is set to true in the tfvars file."""
    path = Path(tfvars_path)
    if not path.is_file():
        raise FileNotFoundError(f"Tfvars file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            # Strip comments
            line = line.split("#")[0].strip()
            match = re.match(r"^dmd_cache_schedule_enabled\s*=\s*(true|false)", line, re.IGNORECASE)
            if match:
                return match.group(1).lower() == "true"
    return False


def validate_action_groups(tfvars_path: Path | str, raw_secret: str | None = None) -> int:
    """Validate action group configuration and export to GITHUB_ENV if valid.

    Returns exit code (0 on success, 1 on fail-closed validation error).
    """
    if raw_secret is None:
        raw_secret = os.environ.get("DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON")

    try:
        schedule_enabled = is_scheduling_enabled_in_tfvars(tfvars_path)
    except Exception as exc:
        print(f"::error::Failed to read tfvars file: {exc}")
        return 1

    github_env = os.environ.get("GITHUB_ENV")

    if schedule_enabled:
        if raw_secret is None or not raw_secret.strip():
            print(
                "::error::dmd_cache_schedule_enabled is true for this target, but "
                "DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON secret is absent or empty. "
                "At least one Azure Monitor Action Group resource ID is required."
            )
            return 1

        try:
            parsed = json.loads(raw_secret)
        except json.JSONDecodeError:
            print(
                "::error::dmd_cache_schedule_enabled is true for this target, but "
                "DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON secret is malformed JSON."
            )
            return 1

        if not isinstance(parsed, list):
            print(
                "::error::dmd_cache_schedule_enabled is true for this target, but "
                "DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON must be a JSON array of strings."
            )
            return 1

        if len(parsed) == 0:
            print(
                "::error::dmd_cache_schedule_enabled is true for this target, but "
                "DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON resolves to an empty list. "
                "At least one Action Group resource ID is required."
            )
            return 1

        for item in parsed:
            if not isinstance(item, str) or not item.strip():
                print(
                    "::error::dmd_cache_schedule_enabled is true for this target, but "
                    "DMD_CACHE_ALERT_ACTION_GROUP_IDS_JSON contains invalid or empty resource IDs."
                )
                return 1

        compact_json = json.dumps(parsed)

        # Mask every ID in GitHub Actions logs
        for item in parsed:
            print(f"::add-mask::{item}")

        if github_env:
            with open(github_env, "a", encoding="utf-8") as f:
                f.write(f"TF_VAR_dmd_cache_alert_action_group_ids={compact_json}\n")

        print(
            f"Successfully validated Action Group configuration: {len(parsed)} "
            "Action Group resource ID(s) configured for dm+d alerts."
        )
        return 0

    else:
        # Scheduling disabled (e.g. Play)
        compact_json = "[]"
        if raw_secret and raw_secret.strip():
            try:
                parsed = json.loads(raw_secret)
                if isinstance(parsed, list):
                    compact_json = json.dumps(parsed)
                    for item in parsed:
                        if isinstance(item, str) and item.strip():
                            print(f"::add-mask::{item}")
            except Exception:
                pass

        if github_env:
            with open(github_env, "a", encoding="utf-8") as f:
                f.write(f"TF_VAR_dmd_cache_alert_action_group_ids={compact_json}\n")

        print("dm+d cache scheduling is disabled for this target. Action Group configuration not required.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dm+d Action Group configuration")
    parser.add_argument("--tfvars-file", required=True, help="Path to target tfvars file")
    args = parser.parse_args()

    exit_code = validate_action_groups(args.tfvars_file)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
