import json
import sys


def verify_plan(plan_file):
    with open(plan_file, "r") as f:
        plan = json.load(f)

    changes = []
    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        if actions and actions != ["no-op"] and actions != ["read"]:
            changes.append(rc)

    if len(changes) == 0:
        print("Guard passed: No changes.")
        return 0

    if len(changes) > 1:
        print(f"Guard failed: Too many changes ({len(changes)}). Only 0 or 1 allowed.")
        return 1

    rc = changes[0]
    address = rc.get("address")
    actions = rc.get("change", {}).get("actions", [])

    if address != "azurerm_key_vault.shared_kv":
        print(f"Guard failed: Resource {address} is changing, which is forbidden.")
        return 1

    if actions != ["update"]:
        print(f"Guard failed: Action {actions} on {address} is forbidden. Only ['update'] is allowed.")
        return 1

    # Verify that ONLY the network_acls.virtual_network_subnet_ids changed.
    before = rc.get("change", {}).get("before", {})
    after = rc.get("change", {}).get("after", {})

    # We want to check that everything except network_acls is identical.
    keys_to_check = set(before.keys()).union(set(after.keys()))

    for key in keys_to_check:
        if key == "network_acls":
            before_acls = before.get(key, [])
            after_acls = after.get(key, [])
            if len(before_acls) != len(after_acls):
                print(f"Guard failed: network_acls structure changed.")
                return 1
            if before_acls and after_acls:
                b_acl = before_acls[0]
                a_acl = after_acls[0]
                acl_keys = set(b_acl.keys()).union(set(a_acl.keys()))
                for ack in acl_keys:
                    if ack == "virtual_network_subnet_ids":
                        continue
                    if b_acl.get(ack) != a_acl.get(ack):
                        print(f"Guard failed: network_acls property '{ack}' changed.")
                        return 1
        else:
            if before.get(key) != after.get(key):
                print(f"Guard failed: Resource property '{key}' changed.")
                return 1

    print(
        "Guard passed: Exactly one safe update to azurerm_key_vault.shared_kv network_acls.virtual_network_subnet_ids."
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python guard_shared_plan.py <plan.json>")
        sys.exit(1)

    sys.exit(verify_plan(sys.argv[1]))
