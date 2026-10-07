import os

from app.gp_connect_config import (
    build_gp_connect_parameters,
    get_gp_connect_inclusions,
)


def test_get_gp_connect_inclusions_defaults():
    # Clear environment variables to test defaults
    os.environ.pop("GP_CONNECT_INCLUDE_ALLERGIES", None)
    os.environ.pop("GP_CONNECT_INCLUDE_MEDICATION", None)
    os.environ.pop("GP_CONNECT_INCLUDE_PROBLEMS", None)
    os.environ.pop("GP_CONNECT_INCLUDE_INVESTIGATIONS", None)
    os.environ.pop("GP_CONNECT_INCLUDE_IMMUNISATIONS", None)

    inclusions = get_gp_connect_inclusions()
    assert inclusions == {
        "include_allergies": True,
        "include_medication": True,
        "include_problems": True,
        "include_investigations": False,
        "include_immunisations": False,
    }


def test_get_gp_connect_inclusions_custom():
    os.environ["GP_CONNECT_INCLUDE_ALLERGIES"] = "false"
    os.environ["GP_CONNECT_INCLUDE_MEDICATION"] = "false"
    os.environ["GP_CONNECT_INCLUDE_PROBLEMS"] = "true"
    os.environ["GP_CONNECT_INCLUDE_INVESTIGATIONS"] = "false"
    os.environ["GP_CONNECT_INCLUDE_IMMUNISATIONS"] = "true"

    inclusions = get_gp_connect_inclusions()
    assert inclusions == {
        "include_allergies": False,
        "include_medication": False,
        "include_problems": True,
        "include_investigations": False,
        "include_immunisations": True,
    }


def test_build_gp_connect_parameters():
    os.environ["GP_CONNECT_INCLUDE_ALLERGIES"] = "true"
    os.environ["GP_CONNECT_INCLUDE_MEDICATION"] = "true"
    os.environ["GP_CONNECT_INCLUDE_PROBLEMS"] = "false"
    os.environ["GP_CONNECT_INCLUDE_INVESTIGATIONS"] = "false"
    os.environ["GP_CONNECT_INCLUDE_IMMUNISATIONS"] = "false"

    parameters = build_gp_connect_parameters(get_gp_connect_inclusions())
    assert parameters == [
        {
            "name": "includeAllergies",
            "part": [{"name": "includeResolvedAllergies", "valueBoolean": False}],
        },
        {
            "name": "includeMedication",
            "part": [{"name": "includePrescriptionIssues", "valueBoolean": False}],
        },
    ]


# ---------------------------------------------------------------------------
# Clinical safety hazard XH-040 (DCB0129 hazard log) / Test Register T-24
# ---------------------------------------------------------------------------
# We must ensure that core structured data (Allergies, Medications, Problems)
# is never silently filtered by date/time, as this suppresses clinical entries.
# However, this test is specifically scoped to those high-risk domains.
# It intentionally does NOT block time-boxing for high-volume domains
# (like Investigations) which may require date filters in the future to prevent timeouts.
# It also does not enforce valueBoolean=False for inclusions, as fetching
# resolved allergies (valueBoolean=True) is a clinically valid expansion of data.


def test_xh_040_core_domains_unfiltered():
    """XH-040: Assert core GP Connect domains carry no date/status filters."""
    os.environ["GP_CONNECT_INCLUDE_ALLERGIES"] = "true"
    os.environ["GP_CONNECT_INCLUDE_MEDICATION"] = "true"
    os.environ["GP_CONNECT_INCLUDE_PROBLEMS"] = "true"

    parameters = build_gp_connect_parameters(get_gp_connect_inclusions())

    # The domains that must NEVER have a date/status filter applied
    core_domains = {"includeAllergies", "includeMedication", "includeProblems"}

    # Substrings that would indicate a silent filter
    filter_tokens = (
        "timeperiod",
        "searchfrom",
        "searchto",
        "fromdate",
        "todate",
        "date",
        "period",
        "status",
    )

    found_core_domains = set()

    for param in parameters:
        param_name = param.get("name", "")
        if param_name in core_domains:
            found_core_domains.add(param_name)
            for part in param.get("part", []):
                part_name = part.get("name", "").lower()
                for token in filter_tokens:
                    assert token not in part_name, (
                        f"Hazard XH-040 Violation: Unexpected filter '{part.get('name')}' "
                        f"applied to core domain '{param_name}'. Core domains must remain unfiltered."
                    )

    assert found_core_domains == core_domains, (
        f"Hazard XH-040 Violation: Missing core domains in output. Expected {core_domains}, found {found_core_domains}"
    )
