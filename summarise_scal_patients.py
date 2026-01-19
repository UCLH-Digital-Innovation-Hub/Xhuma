import asyncio
import csv
from typing import Any, Dict, List, Optional

from app.pds.pds import lookup_patient

NHS_NUMBERS = [
    "9658218873",
    "9658218903",
    "9690937286",
    "9690937294",
    "9690937308",
    "9690937375",
    "9690938096",
    "9690938118",
    "9690938207",
    "9690938533",
    "9690938541",
    "9690938576",
    "9690938681",
]


NHS_NUMBERS = [
    "9658218873",
    "9658218903",
    "9690937286",
    "9690937294",
    "9690937308",
    "9690937375",
    "9690938096",
    "9690938118",
    "9690938207",
    "9690938533",
    "9690938541",
    "9690938576",
    "9690938681",
    "9999999999",
]


NHS_NUMBER_SYSTEM = "https://fhir.nhs.uk/Id/nhs-number"


def find_patient(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Accept either a Patient resource or a Bundle containing one."""
    if payload.get("resourceType") == "Patient":
        return payload

    if payload.get("resourceType") == "Bundle":
        for entry in payload.get("entry", []) or []:
            res = (entry or {}).get("resource") or {}
            if isinstance(res, dict) and res.get("resourceType") == "Patient":
                return res

    return None


def get_nhs_number(patient: Dict[str, Any]) -> Optional[str]:
    for ident in patient.get("identifier", []) or []:
        if (
            isinstance(ident, dict)
            and ident.get("system") == NHS_NUMBER_SYSTEM
            and ident.get("value")
        ):
            return str(ident["value"])
    return None


def get_first_last(patient: Dict[str, Any]) -> tuple[str, str]:
    names = patient.get("name", []) or []
    if isinstance(names, dict):
        names = [names]

    if not names:
        return "", ""

    n0 = names[0]  # matches your example: name[0]
    family = n0.get("family") if isinstance(n0.get("family"), str) else ""
    given = n0.get("given") or []
    if isinstance(given, str):
        given = [given]
    given = [g for g in given if isinstance(g, str)]

    first = given[0] if given else ""
    return first, family


def get_dob(patient: Dict[str, Any]) -> str:
    dob = patient.get("birthDate")
    return str(dob) if dob else ""


async def main():
    rows: List[List[str]] = []

    for nhsno in NHS_NUMBERS:
        try:
            patient = await lookup_patient(nhsno)  # <-- your function

            if not patient:
                rows.append([nhsno, "NOT FOUND", "", ""])
                continue

            out_nhs = get_nhs_number(patient) or nhsno
            first, last = get_first_last(patient)
            dob = get_dob(patient)

            rows.append([out_nhs, first, last, dob])

        except Exception as e:
            rows.append([nhsno, "ERROR", str(e), ""])

    with open("patients.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["nhs_number", "first_name", "last_name", "dob"])
        writer.writerows(rows)


if __name__ == "__main__":
    asyncio.run(main())
