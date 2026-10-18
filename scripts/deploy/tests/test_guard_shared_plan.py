import json
import os
import tempfile

from scripts.deploy.guard_shared_plan import verify_plan


def create_plan_file(changes):
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        json.dump({"resource_changes": changes}, f)
        return f.name


TARGET = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/app-subnet"


def test_guard_noop():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {"actions": ["no-op"]},
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 0
    finally:
        os.remove(plan_file)


def test_guard_current_target_added_accepted():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["id1", TARGET]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 0
    finally:
        os.remove(plan_file)


def test_guard_existing_subnet_removed_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1", TARGET]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


def test_guard_one_removed_target_added_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": [TARGET]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


def test_guard_wrong_subnet_added_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["id1", "wrong-target"]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


def test_guard_two_subnets_added_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["id1", TARGET, "wrong-target"]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


def test_guard_case_only_differences_handled_correctly():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["ID1", TARGET.upper()]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 0
    finally:
        os.remove(plan_file)


def test_guard_ip_rule_change_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"ip_rules": ["1.1.1.1/32"], "virtual_network_subnet_ids": ["id1"]}]},
                    "after": {
                        "network_acls": [{"ip_rules": ["2.2.2.2/32"], "virtual_network_subnet_ids": ["id1", TARGET]}]
                    },
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


def test_guard_other_kv_property_change_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"tags": {"env": "prod"}, "network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {
                        "tags": {"env": "dev"},
                        "network_acls": [{"virtual_network_subnet_ids": ["id1", TARGET]}],
                    },
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


def test_guard_delete_create_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {"actions": ["delete"]},
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)

    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {"actions": ["create"]},
            }
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


def test_guard_second_resource_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["id1", TARGET]}]},
                },
            },
            {
                "address": "azurerm_resource_group.rg",
                "change": {"actions": ["update"]},
            },
        ]
    )
    try:
        assert verify_plan(plan_file, TARGET) == 1
    finally:
        os.remove(plan_file)


ACA_TARGET = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/ca-subnet"


def test_guard_app_and_aca_subnets_added_accepted():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["id1", TARGET, ACA_TARGET]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, [TARGET, ACA_TARGET]) == 0
        assert verify_plan(plan_file, f"{TARGET},{ACA_TARGET}") == 0
    finally:
        os.remove(plan_file)


def test_guard_app_and_aca_subnets_with_unknown_rejected():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {
                        "network_acls": [{"virtual_network_subnet_ids": ["id1", TARGET, ACA_TARGET, "rogue-subnet"]}]
                    },
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file, [TARGET, ACA_TARGET]) == 1
    finally:
        os.remove(plan_file)


def test_guard_play_int_shared_hcl_enforcement():
    plan_file = create_plan_file([])
    try:
        assert verify_plan(plan_file, TARGET, target_id="play", shared_backend_file="infra/backends/shared.hcl") == 0
        assert verify_plan(plan_file, TARGET, target_id="int", shared_backend_file="infra/backends/shared.hcl") == 0
        assert verify_plan(plan_file, TARGET, target_id="play", shared_backend_file="infra/backends/play.hcl") == 1
        assert verify_plan(plan_file, TARGET, target_id="int", shared_backend_file="infra/backends/int.hcl") == 1
    finally:
        os.remove(plan_file)


def test_guard_prd_backend_separation():
    plan_file = create_plan_file([])
    try:
        assert verify_plan(plan_file, TARGET, target_id="prd", shared_backend_file="infra/backends/prd.hcl") == 0
        assert verify_plan(plan_file, TARGET, target_id="prd", shared_backend_file="infra/backends/shared.hcl") == 1
    finally:
        os.remove(plan_file)
