import os

# get environmental variables for gpconnect query inclusions


def get_gp_connect_inclusions():
    def read_bool_flag(env_var: str, default: bool) -> bool:
        value = os.getenv(env_var)
        if value is None:
            return default
        return value.strip().lower() == "true"

    return {
        "include_allergies": read_bool_flag("GP_CONNECT_INCLUDE_ALLERGIES", True),
        "include_medication": read_bool_flag("GP_CONNECT_INCLUDE_MEDICATION", True),
        "include_problems": read_bool_flag("GP_CONNECT_INCLUDE_PROBLEMS", True),
        "include_investigations": read_bool_flag("GP_CONNECT_INCLUDE_INVESTIGATIONS", False),
        "include_immunisations": read_bool_flag("GP_CONNECT_INCLUDE_IMMUNISATIONS", False),
    }


def build_gp_connect_parameters(inclusions: dict) -> list[dict]:
    parameters = []

    if inclusions.get("include_allergies", True):
        parameters.append(
            {
                "name": "includeAllergies",
                "part": [{"name": "includeResolvedAllergies", "valueBoolean": False}],
            }
        )

    if inclusions.get("include_medication", True):
        parameters.append(
            {
                "name": "includeMedication",
                "part": [{"name": "includePrescriptionIssues", "valueBoolean": False}],
            }
        )

    if inclusions.get("include_problems", True):
        parameters.append({"name": "includeProblems"})

    if inclusions.get("include_investigations", True):
        parameters.append({"name": "includeInvestigations"})
    if inclusions.get("include_immunisations", True):
        parameters.append({"name": "includeImmunisations"})

    return parameters


GP_CONNECT_PARAMETERS = build_gp_connect_parameters(inclusions=get_gp_connect_inclusions())
