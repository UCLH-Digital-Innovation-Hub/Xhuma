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
