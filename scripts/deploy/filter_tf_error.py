import re
import sys


def main():
    if len(sys.argv) < 3:
        print("Usage: python filter_tf_error.py <exit_code> <plan_output_file>")
        sys.exit(1)

    exit_code = sys.argv[1]
    filename = sys.argv[2]

    print(f"Terraform Exit Code: {exit_code}")

    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        print("Could not read plan output.")
        sys.exit(0)

    found_diagnostics = False

    # We want to deduplicate matches to avoid spamming the summary
    seen_sources = set()
    seen_resources = set()
    seen_codes = set()
    seen_statuses = set()

    for line in lines:
        # Match source filename and line
        source_match = re.search(r"on ([\w\.\-\/]+) line (\d+)", line)
        if source_match:
            source = f"{source_match.group(1)} line {source_match.group(2)}"
            if source not in seen_sources:
                print(f"Source Location: {source}")
                seen_sources.add(source)
                found_diagnostics = True

        # Match resource address
        # Looks for azurerm_type.name or azurerm_type "name"
        res_match = re.search(r"(azurerm_[a-zA-Z0-9_]+)[\"'\s\.]+([a-zA-Z0-9_]+)", line)
        if res_match:
            resource = f"{res_match.group(1)}.{res_match.group(2)}"
            if resource not in seen_resources:
                print(f"Resource Address: {resource}")
                seen_resources.add(resource)
                found_diagnostics = True

        # Match Azure error codes/status
        code_match = re.search(r"Code=[\"']?([A-Za-z0-9_]+)[\"']?", line)
        if code_match:
            code = code_match.group(1)
            if code not in seen_codes:
                print(f"Azure Error Code: {code}")
                seen_codes.add(code)
                found_diagnostics = True

        status_match = re.search(r"Status=([0-9]+)", line)
        if status_match:
            status = status_match.group(1)
            if status not in seen_statuses:
                print(f"Azure Status: {status}")
                seen_statuses.add(status)
                found_diagnostics = True

    if not found_diagnostics:
        print("Unrecognised Error: The error could not be safely classified. Raw diagnostics are retained securely.")


if __name__ == "__main__":
    main()
