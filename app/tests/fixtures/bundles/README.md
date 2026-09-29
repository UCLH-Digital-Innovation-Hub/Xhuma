# GP Connect allergy test-pack bundles

These 13 fixtures are the original successful GP Connect response bodies for the EMIS and TPP clinical test-pack patients, retrieved from the NHS integration environment on 24 September 2026. They are stored unchanged as `<NHS number>.json`, alongside the existing bundle fixtures.

The calls requested allergies with `includeResolvedAllergies=false`; other clinical domains were excluded. They cover six EMIS and seven TPP test patients, with 54 AllergyIntolerance records and three empty allergy lists. Failed connection attempts and local request/audit logs are not fixtures.

| Supplier | Fixture | Allergy records |
| -------- | ------- | --------------- |
| EMIS | [9738345251.json](9738345251.json) | 4 |
| EMIS | [9738345367.json](9738345367.json) | 13 |
| EMIS | [9738345286.json](9738345286.json) | 1 |
| EMIS | [9738345510.json](9738345510.json) | 0 |
| EMIS | [9738345308.json](9738345308.json) | 4 |
| EMIS | [9738345316.json](9738345316.json) | 4 |
| TPP | [9738345278.json](9738345278.json) | 5 |
| TPP | [9738345375.json](9738345375.json) | 11 |
| TPP | [9738345324.json](9738345324.json) | 0 |
| TPP | [9738345529.json](9738345529.json) | 0 |
| TPP | [9738345340.json](9738345340.json) | 3 |
| TPP | [9738345359.json](9738345359.json) | 6 |
| TPP | [9738345405.json](9738345405.json) | 3 |

All 13 patients supply official names only. The EMIS fixtures include two explicit no-known-allergy assertions; one coexists with positive allergy records. Empty lists remain distinct from these assertions. Supplied codes, dates, statuses and notes may differ from the workbook expectations, and the EMIS lists include incomplete-transfer warnings. These source differences are intentionally preserved.

See [Allergy Mapping overview](../../../../docs/allergy_mapping.md) for conversion behaviour and clinical validation limitations. These fixtures support offline conversion checks; successful XML generation does not establish Epic ingestion or clinical acceptance.

## Refreshing the fixtures

From the repository root:

```bash
.venv/bin/python scripts/update_allergy_fixtures.py --org-asid 200000002574
```

The script reads `.env` without overriding existing environment variables. Configure `API_KEY`, `ORG_CODE`, `ORG_ASID` (or the CLI argument) and `KID`, plus the usual JWT signing key and NHS TLS certificates under `keys/`. It always uses the integration environment and direct TLS, with active allergies requested and other domains excluded. The ASID above worked for the original retrieval.

Redis and the migrated audit database must be reachable using the application's normal settings. `DATABASE_URL` can override the `POSTGRES_*` settings, for example when a local database does not use SSL. The script uses the shared implementation behind `gpconnect`, retains audit persistence and CCDA conversion, and caches the resulting documents normally. It does not start services or apply database migrations. The audit caller defaults to the local username; use `--subject` to supply the appropriate caller identity.

All 13 patients are refreshed sequentially. Each original response body is checked for valid FHIR, the requested patient's NHS number and an allergy list before its fixture is atomically replaced. Empty allergy lists are valid. Failed requests, conversion failures and mismatched responses leave that patient's existing fixture unchanged; other patients continue. The final summary reports the outcome and any failure produces a nonzero exit code. Temporary request/response logs are deleted after each patient.

Review the resulting JSON diff: the counts and source-data observations above describe the original snapshot and may change when supplier records are updated. Refreshing fixtures does not stage or commit them.
