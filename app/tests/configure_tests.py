import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
BUNDLE_DIR = FIXTURE_DIR / "bundles"
PDS_DIR = FIXTURE_DIR / "pdsresults"


def get_nhs_ids():
    """Returns sorted list of all NHS numbers found in bundles (expecting .json files)."""
    return sorted(
        f.stem for f in BUNDLE_DIR.glob("*.json") if (PDS_DIR / f.name).exists()
    )


def load_bundle(nhsno):
    with open(BUNDLE_DIR / f"{nhsno}.json") as f:
        return json.load(f)


def load_pds(nhsno):
    with open(PDS_DIR / f"{nhsno}.json") as f:
        return json.load(f)
