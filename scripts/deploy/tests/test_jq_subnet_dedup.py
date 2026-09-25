import json
import subprocess


def test_jq_subnet_dedup_case_insensitive():
    jq_script = """
    [($acls.virtualNetworkRules[]?.id // empty), $target_subnet] | map(ascii_downcase) | unique
    """

    live_acls = {
        "virtualNetworkRules": [
            {
                "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/app-subnet"
            }
        ]
    }

    target_subnet = "/subscriptions/sub/resourcegroups/rg/providers/microsoft.network/virtualnetworks/vnet/subnets/app-subnet"  # Mixed case variant

    process = subprocess.run(
        ["jq", "-n", "--arg", "target_subnet", target_subnet, "--argjson", "acls", json.dumps(live_acls), jq_script],
        capture_output=True,
        text=True,
        check=True,
    )

    result = json.loads(process.stdout)

    # We should have exactly 1 item due to deduplication, and it should be lowercase
    assert len(result) == 1
    assert result[0] == target_subnet.lower()
