import json
import sys


def main():
    if len(sys.argv) != 3:
        print("Usage: python select_targets.py <targets_file> <branch>")
        sys.exit(1)

    targets_path = sys.argv[1]
    branch = sys.argv[2]

    with open(targets_path, "r") as f:
        inventory = json.load(f)

    selected_targets = []

    for t in inventory["targets"]:
        if not t.get("enabled", False):
            continue
        if t.get("hold", False):
            continue

        stage = t.get("stage")

        # Require source_branch matches the invoking branch
        if t.get("source_branch") != branch:
            continue

        # Enforce strict branch-to-stage mapping
        if branch == "rehearsal/play-deployment":
            if stage == "play":
                selected_targets.append(t)
        elif branch == "dev":
            if stage == "play":
                selected_targets.append(t)
        elif branch == "int":
            if stage == "int":
                selected_targets.append(t)
        elif branch == "main":
            if stage == "prd":
                selected_targets.append(t)

    if not selected_targets:
        print(
            f"Error: No valid, enabled targets found for branch '{branch}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Print as a compact JSON array so it can be captured by GitHub Actions
    print(json.dumps(selected_targets))


if __name__ == "__main__":
    main()
