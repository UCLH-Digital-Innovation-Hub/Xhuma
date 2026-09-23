import json
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

    seen_sources = set()
    seen_resources = set()
    seen_errors = set()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            log_entry = json.loads(line)
            if log_entry.get("type") == "diagnostic" and log_entry.get("diagnostic", {}).get("severity") == "error":
                diag = log_entry.get("diagnostic", {})

                # Extract address
                address = diag.get("address")
                if address and address not in seen_resources:
                    print(f"Resource Address: {address}")
                    seen_resources.add(address)
                    found_diagnostics = True

                # Extract source location
                range_info = diag.get("range", {})
                source_filename = range_info.get("filename")
                start_line = range_info.get("start", {}).get("line")
                if source_filename and start_line:
                    source = f"{source_filename} line {start_line}"
                    if source not in seen_sources:
                        print(f"Source Location: {source}")
                        seen_sources.add(source)
                        found_diagnostics = True

                # Extract safe labels from summary/detail
                summary = diag.get("summary", "")
                detail = diag.get("detail", "")
                combined = f"{summary} {detail}"

                label = None
                if "AuthorizationFailed" in combined or "403" in combined:
                    label = "Authorisation Error"
                elif "SubscriptionNotFound" in combined or "subscription" in combined.lower():
                    label = "Subscription Access Error"
                elif "MissingResourceProviderRegistration" in combined or "registration" in combined.lower():
                    label = "Provider Registration Error"
                elif (
                    "undeclared input variable" in combined
                    or "Unsupported argument" in combined
                    or "expected" in combined
                ):
                    label = "Configuration Error"
                elif "Code=" in combined:
                    code_match = re.search(r"Code=[\"']?([A-Za-z0-9_]+)[\"']?", combined)
                    if code_match:
                        label = f"Azure Error Code: {code_match.group(1)}"
                elif "Status=" in combined:
                    status_match = re.search(r"Status=([0-9]+)", combined)
                    if status_match:
                        label = f"Azure Status: {status_match.group(1)}"

                if label and label not in seen_errors:
                    print(f"Error Type: {label}")
                    seen_errors.add(label)
                    found_diagnostics = True

        except json.JSONDecodeError:
            # Fallback for plain text, but strictly look for Azure codes to avoid dumping raw text
            code_match = re.search(r"Code=[\"']?([A-Za-z0-9_]+)[\"']?", line)
            if code_match:
                code = code_match.group(1)
                label = f"Azure Error Code: {code}"
                if label not in seen_errors:
                    print(f"Error Type: {label}")
                    seen_errors.add(label)
                    found_diagnostics = True

            status_match = re.search(r"Status=([0-9]+)", line)
            if status_match:
                status = status_match.group(1)
                label = f"Azure Status: {status}"
                if label not in seen_errors:
                    print(f"Error Type: {label}")
                    seen_errors.add(label)
                    found_diagnostics = True

    if not found_diagnostics:
        print(
            "Unrecognised Error: The error could not be safely classified. No persistence mechanism is currently implemented for raw diagnostics."
        )


if __name__ == "__main__":
    main()
