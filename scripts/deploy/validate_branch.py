import sys


def main():
    if len(sys.argv) < 3:
        print("Usage: validate_branch.py <workflow_type> <github_event_name> [github_ref] [github_base_ref]")
        sys.exit(1)

    workflow_type = sys.argv[1]
    event_name = sys.argv[2]
    ref = sys.argv[3] if len(sys.argv) > 3 else ""
    base_ref = sys.argv[4] if len(sys.argv) > 4 else ""

    if workflow_type == "cd":
        if ref not in ["refs/heads/main"]:
            print(f"::error::Unsupported branch for CD pipeline: {ref}")
            sys.exit(1)

    elif workflow_type == "infra":
        if event_name in ["push", "workflow_dispatch"]:
            if ref not in ["refs/heads/main"]:
                print(f"::error::Unsupported branch for push/dispatch in infra pipeline: {ref}")
                sys.exit(1)
        elif event_name == "pull_request":
            if base_ref not in ["main"]:
                print(f"::error::Unsupported base branch for PR in infra pipeline: {base_ref}")
                sys.exit(1)
        else:
            print(f"::error::Unsupported event: {event_name}")
            sys.exit(1)
    else:
        print(f"::error::Unknown workflow type: {workflow_type}")
        sys.exit(1)


if __name__ == "__main__":
    main()
