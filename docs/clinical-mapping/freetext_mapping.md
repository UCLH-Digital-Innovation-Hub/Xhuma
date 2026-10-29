# Freetext mapping

Xhuma can add structured FHIR dosage information when a medicine has only
free-text dosage instructions. It looks up the supplied text in a fixed mapping
derived from the canonical CSV, then passes the enriched dosage through the
existing C-CDA medication converter and dm+d processing.

## Canonical source

The **Mapping Table file - FT to Bucket to FHIR.csv** received on **6 October**
from the NHS England Digital Medicines Team is the authority for this mapping. The unchanged file is retained in
[`scripts/data/nhs_dosage`](../../scripts/data/nhs_dosage/).
Its columns contain the dosage text, a bucket label, and the corresponding FHIR
JSON. The FHIR JSON is taken directly from the source; bucket labels are retained
for traceability and are not used to calculate or infer dosage information.

The accepted source SHA-256 is:

```text
3316f2f9134c605c00636171aa05fd066fe24aca08229ee6327fd07c4aebc54c
```

[`scripts/build_dosage_mapping.py`](../../scripts/build_dosage_mapping.py) checks
this hash before parsing the CSV or writing output. A different file, including
one with only a newline change, is rejected. There is no checksum override flag.
The original CSV must remain unchanged.

## Generating and loading the dictionary

Run the builder from the repository root:

```bash
python scripts/build_dosage_mapping.py
```

An explicit source path is also accepted, provided its bytes have the same
pinned checksum:

```bash
python scripts/build_dosage_mapping.py \
  "/path/to/Mapping Table file - FT to Bucket to FHIR.csv"
```

The builder consolidates **45,902 source rows into 1,693 lookup keys** and writes
[`dosage_mapping.json`](../../app/ccda/data/dosage_mapping.json) and
[`dosage_provenance.json`](../../app/ccda/data/dosage_provenance.json). Provenance
records the source filename, checksums, row counts, and normalization rules.
Conflicting mappings for the same normalized key cause the build to fail.

These files are generated artifacts and must not be edited by hand. Changes to
the accepted source require an explicit code change and review of the pinned
checksum. Git attributes preserve the exact bytes across checkouts.

[`app/ccda/dosage_lookup.py`](../../app/ccda/dosage_lookup.py) verifies the generated
mapping's pinned checksum and loads the dictionary **once per application
process**, when the medication converter is imported. The cached dictionary and
its nested values are immutable. Each
successful lookup produces an independent copy for that conversion; changing
the copy cannot change the cached mapping. Conversion requests do not reread
the CSV or rebuild the dictionary.

The existing Docker `COPY app` step includes the generated files in the running
container. The canonical CSV and builder are only needed to regenerate them.

## Matching dosage text

Matching lowercases the text, trims surrounding whitespace, and collapses runs
of whitespace. Punctuation, abbreviations, and word/number variants remain
distinct. There is no fuzzy matching or interpretation of unfamiliar text.

For example, `  ONE   twice A day ` matches the key `one twice a day` and returns
the source mapping with a dose quantity of 1 and a frequency of twice per day:

```json
{
  "text": "  ONE   twice A day ",
  "doseQuantity": {"value": 1},
  "timing": {
    "repeat": {"frequency": 2, "period": 1, "periodUnit": "d"}
  }
}
```

The returned `text` preserves the caller's exact original wording and spacing.
All other FHIR values, codes, and units come from the source mapping unchanged.
The quantity above is unitless because that is how the canonical source records
this instruction; subsequent dm+d processing uses the existing conversion rules.

## When the fallback applies

Before converting a `MedicationStatement`, Xhuma checks its dosage list. If
**any dosage already contains a structured field**, it leaves the entire list
as supplied. This includes dose quantities or ranges, timing, route, method,
site, additional instructions, and an explicit `asNeededBoolean: false`.
Text, patient instructions, IDs, extensions, and sequence numbers alone do not
prevent lookup.

[`medication.py`](../../app/ccda/entries/medication.py) invokes the fallback with
`dosages = mapped_dosages(entry.dosage)`. All subsequent dosage processing uses
this local list, including dose quantity, timing, PRN handling, and eligible
dm+d ingredient-dose conversion.

When the list contains only narrative instructions, Xhuma looks up each dosage
text and adds the mapped FHIR fields to a local copy. Patient instructions,
metadata, original text, and dosage order are preserved. The source FHIR
statement and its associated request remain unchanged.

Unmatched or empty text receives no inferred structure. Missing dosage lists
are handled safely without inventing a dose. The enriched dosages use the
converter's existing handling of multiple instructions and supported C-CDA
fields. In particular, this fallback does not add C-CDA dose-range support or
correct the clinical content of the supplied mappings.

## Verification

```bash
python -m pytest -q app/tests/test_dosage_lookup.py app/tests/test_meds.py \
  app/tests/test_prn_meds.py app/tests/test_medication_sections.py \
  app/tests/test_ccda_html_validation.py
```

The tests compare every source row's complete parsed FHIR JSON and bucket with
the generated lookup, check all 1,693 templates against the application's STU3
FHIR client, and verify reproducible generation, checksum rejection,
immutability, structured-dosage precedence, and conversion through dm+d.

See [dm+d mapping](dm+d_mapping.md) for the subsequent product-to-ingredient dose
conversion.
