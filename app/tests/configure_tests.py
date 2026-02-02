import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock

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


@pytest.fixture
def fake_pg_pool():
    # Connection returned by the context manager
    conn = AsyncMock()

    # The async context manager returned by pool.acquire()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)

    # Pool whose acquire() returns the CM *synchronously*
    pool = MagicMock()
    pool.acquire.return_value = acquire_cm

    return pool, conn