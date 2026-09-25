import json
import tempfile
import os
import pytest
from scripts.deploy.guard_shared_plan import verify_plan


def create_plan_file(changes):
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        json.dump({"resource_changes": changes}, f)
        return f.name


def test_guard_noop():
    plan_file = create_plan_file([{"address": "azurerm_key_vault.shared_kv", "change": {"actions": ["no-op"]}}])
    try:
        assert verify_plan(plan_file) == 0
    finally:
        os.remove(plan_file)


def test_guard_valid_update():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {
                        "name": "kv",
                        "network_acls": [
                            {
                                "default_action": "Deny",
                                "ip_rules": ["1.1.1.1/32"],
                                "virtual_network_subnet_ids": ["id1"],
                            }
                        ],
                    },
                    "after": {
                        "name": "kv",
                        "network_acls": [
                            {
                                "default_action": "Deny",
                                "ip_rules": ["1.1.1.1/32"],
                                "virtual_network_subnet_ids": ["id1", "id2"],
                            }
                        ],
                    },
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file) == 0
    finally:
        os.remove(plan_file)


def test_guard_invalid_ip_rules_update():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {
                        "name": "kv",
                        "network_acls": [
                            {
                                "default_action": "Deny",
                                "ip_rules": ["1.1.1.1/32"],
                                "virtual_network_subnet_ids": ["id1"],
                            }
                        ],
                    },
                    "after": {
                        "name": "kv",
                        "network_acls": [
                            {
                                "default_action": "Deny",
                                "ip_rules": ["1.1.1.1/32", "2.2.2.2/32"],
                                "virtual_network_subnet_ids": ["id1"],
                            }
                        ],
                    },
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file) == 1
    finally:
        os.remove(plan_file)


def test_guard_invalid_delete():
    plan_file = create_plan_file([{"address": "azurerm_key_vault.shared_kv", "change": {"actions": ["delete"]}}])
    try:
        assert verify_plan(plan_file) == 1
    finally:
        os.remove(plan_file)


def test_guard_invalid_create():
    plan_file = create_plan_file([{"address": "azurerm_key_vault.shared_kv", "change": {"actions": ["create"]}}])
    try:
        assert verify_plan(plan_file) == 1
    finally:
        os.remove(plan_file)


def test_guard_invalid_other_attribute_update():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"tags": {"env": "prod"}, "network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"tags": {"env": "dev"}, "network_acls": [{"virtual_network_subnet_ids": ["id1", "id2"]}]},
                },
            }
        ]
    )
    try:
        assert verify_plan(plan_file) == 1
    finally:
        os.remove(plan_file)


def test_guard_invalid_second_resource():
    plan_file = create_plan_file(
        [
            {
                "address": "azurerm_key_vault.shared_kv",
                "change": {
                    "actions": ["update"],
                    "before": {"network_acls": [{"virtual_network_subnet_ids": ["id1"]}]},
                    "after": {"network_acls": [{"virtual_network_subnet_ids": ["id1", "id2"]}]},
                },
            },
            {"address": "azurerm_resource_group.rg", "change": {"actions": ["update"]}},
        ]
    )
    try:
        assert verify_plan(plan_file) == 1
    finally:
        os.remove(plan_file)
