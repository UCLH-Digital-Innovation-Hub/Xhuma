# GP Connect bundle fixtures

- [Allergy test-pack bundles](allergies/README.md): 13 allergy-only EMIS and TPP responses.
- [Investigation test-pack bundles](investigations/README.md): four investigation-only EMIS and TPP responses.

The JSON files in this directory are the existing general-purpose bundle fixtures used by shared converter tests. Domain-specific test packs are kept separately because the same patient can have different responses depending on the requested clinical sections.


## Refreshing test-pack fixtures

The existing script now covers both supplied **GP Connect ARS Clinical Test Pack — sent to UCLH 23 Sept** workbooks. The reviewed patient manifest is `scripts/test_pack_patients.json`; it records workbook, sheet and cell references without copying patient names or addresses. Updating a workbook does not automatically update the manifest.

```bash
# Preview all identified patients and coverage gaps; no credentials needed.
.venv/bin/python scripts/update_allergy_fixtures.py --dry-run

# Refresh selected domains, optionally restricted to one supplier.
.venv/bin/python scripts/update_allergy_fixtures.py --domain allergies investigations --supplier TPP --org-asid 200000002574

# Refresh all supported, identified test-pack patients.
.venv/bin/python scripts/update_allergy_fixtures.py --domain all --org-asid 200000002574
```

Options for `--domain` are `allergies`, `medication`, `investigations`, `immunisations`, `problems`, `uncategorised`, `additional`, and `all` (the default). Multiple selections are accepted. `additional` requests all five supported clinical domains for patients in the Additional tests sheets. `--output-dir` overrides the parent fixture directory; each response is stored under `<domain>/<NHS number>.json`. The same patient in different domains receives separate requests and files. Repeated medication scenarios for one patient require only one medication request.

The packs identify 13 allergy patients, 2 medication patients, 4 investigation patients, 2 uncategorised-data patients and 3 additional-test patients. Problems and immunisation patient headers have no NHS numbers. The TPP dose-syntax sheet names Lester Egan without an NHS number, and an additional-test patient has an invalid 11-digit identifier. These gaps are reported; no identities are guessed. Uncategorised-data requests are not supported by the application's current request/converter configuration, so they are explicitly skipped and count as failures during an actual refresh.

The script always uses the integration environment and the application's audited GP Connect fetch/conversion path. Credentials, signing/TLS keys, Redis and the migrated audit database must be configured as described in the [allergy refresh instructions](allergies/README.md#refreshing-the-fixtures). Existing active-only allergy and prescription-issue request settings are retained.

A response replaces its fixture atomically only after successful audit/conversion, FHIR parsing, patient matching and requested-list validation. An empty domain list is valid. Access-denied responses and malformed source records do not replace fixtures. This includes the known Bob Theresa investigation parser failure and the non-consenting additional-test patient: they remain reported failures, rather than successful bundle refreshes. Other requests continue, and failures produce a nonzero exit code. Temporary logs are removed. No live calls are made by `--dry-run`.

Review the JSON diffs after a refresh. Existing assessment documents and investigation `manifest.json` describe historical retrievals; the script does not rewrite that evidence or stage/commit fixtures.
