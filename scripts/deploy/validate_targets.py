import json
import os
import re
import sys
import jsonschema


def validate_path(path_str):
    if not path_str or ".." in path_str or path_str.startswith("/"):
        return False
    if not re.match(r"^[a-zA-Z0-9_/-]+\.[a-zA-Z0-9]+$", path_str):
        return False
    return True


def main():
    if len(sys.argv) != 3:
        print("Usage: python validate_targets.py <schema_file> <targets_file>")
        sys.exit(1)

    schema_path = sys.argv[1]
    targets_path = sys.argv[2]

    # Resolve from repo root
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

    with open(schema_path, "r") as f:
        schema = json.load(f)

    with open(targets_path, "r") as f:
        targets = json.load(f)

    try:
        jsonschema.validate(instance=targets, schema=schema)
    except jsonschema.exceptions.ValidationError as err:
        print("Validation error:", err)
        sys.exit(1)

    ids = []
    app_names = []
    backends = []

    for t in targets["targets"]:
        if not t.get("enabled", False):
            continue

        t_id = t["id"]
        app_name = t["app_service_name"]

        # Check placeholders
        for k, v in t.items():
            if isinstance(v, str) and "TODO" in v:
                print(
                    f"Validation error: Target {t_id} contains unresolved placeholder in {k}: {v}"
                )
                sys.exit(1)

        # Validate paths
        tfvars = t.get("tfvars_file", "")
        backend = t.get("backend_file", "")

        if not validate_path(tfvars):
            print(f"Validation error: Target {t_id} has invalid tfvars_file path.")
            sys.exit(1)

        if not validate_path(backend):
            print(f"Validation error: Target {t_id} has invalid backend_file path.")
            sys.exit(1)

        if not os.path.isfile(os.path.join(repo_root, tfvars)):
            print(
                f"Validation error: Target {t_id} tfvars_file {tfvars} does not exist."
            )
            sys.exit(1)

        if not os.path.isfile(os.path.join(repo_root, backend)):
            print(
                f"Validation error: Target {t_id} backend_file {backend} does not exist."
            )
            sys.exit(1)

        # Extract actual backend coordinates
        backend_content = ""
        with open(os.path.join(repo_root, backend), "r") as bf:
            backend_content = bf.read()

        coord = {}
        for line in backend_content.splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                coord[k.strip()] = v.strip().strip('"')

        # Required backend fields
        b_rg = coord.get("resource_group_name")
        b_sa = coord.get("storage_account_name")
        b_container = coord.get("container_name")
        b_key = coord.get("key")

        if not (b_rg and b_sa and b_container and b_key):
            print(
                f"Validation error: Target {t_id} backend missing required coordinates in {backend}"
            )
            sys.exit(1)

        if b_rg != t.get("resource_group"):
            print(
                f"Validation error: Target {t_id} backend RG ({b_rg}) does not match target RG ({t.get('resource_group')})"
            )
            sys.exit(1)

        backend_coord = f"{b_rg}/{b_sa}/{b_container}/{b_key}"
        backends.append(backend_coord)

        ids.append(t_id)
        app_names.append(app_name)

    if len(ids) != len(set(ids)):
        print("Validation error: Duplicate enabled target IDs found.")
        sys.exit(1)

    if len(app_names) != len(set(app_names)):
        print("Validation error: Duplicate enabled app service names found.")
        sys.exit(1)

    if len(backends) != len(set(backends)):
        print("Validation error: Duplicate enabled backend coordinates found.")
        sys.exit(1)

    print("Validation successful: targets match schema and constraints.")


if __name__ == "__main__":
    main()
