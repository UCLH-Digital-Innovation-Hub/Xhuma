"""Build the dosage lookup only from the unchanged CSV received on 6 October.

The CSV is canonical. Never edit the generated mapping or infer FHIR from buckets.
This script uses only the standard library and does not import the application.
"""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

SOURCE_SHA256 = "3316f2f9134c605c00636171aa05fd066fe24aca08229ee6327fd07c4aebc54c"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "scripts/data/nhs_dosage/Mapping Table file - FT to Bucket to FHIR.csv"
DEFAULT_OUTPUT = ROOT / "app/ccda/data"


def consolidate(source: Path, output: Path) -> dict:
    """Reject different source bytes before parsing or writing any output."""
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Source SHA-256 mismatch: expected {SOURCE_SHA256}, got {digest}")

    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    if reader.fieldnames != ["dosage", "buckets", "fhir_json"]:
        raise ValueError("Expected dosage,buckets,fhir_json columns")
    mappings = {}
    count = 0
    for line, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Malformed CSV record {line}")
        count += 1
        key = " ".join(row["dosage"].lower().split())
        if not key:
            raise ValueError(f"Empty dosage in record {line}")
        fhir = json.loads(row["fhir_json"])
        if not isinstance(fhir, dict) or fhir.get("text") != row["dosage"]:
            raise ValueError(f"Invalid FHIR dosage in record {line}")
        # Only text is normalized; all other FHIR values and bucket labels are canonical.
        fhir["text"] = key
        value = {"buckets": row["buckets"], "fhir": fhir}
        if key in mappings and mappings[key] != value:
            raise ValueError(f"Conflicting mappings for {key!r} in record {line}")
        mappings[key] = value
    if not mappings:
        raise ValueError("Source contains no mappings")

    generated = (json.dumps(mappings, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    metadata = {
        "source_filename": source.name,
        "source_received": "6 October",
        "source_sha256": digest,
        "source_rows": count,
        "consolidated_rows": len(mappings),
        "normalization": "lowercase; trim and collapse whitespace; preserve punctuation",
        "mapping_sha256": hashlib.sha256(generated).hexdigest(),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "dosage_mapping.json").write_bytes(generated)
    (output / "dosage_provenance.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        metadata = consolidate(args.source, args.output)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(metadata, indent=2))
