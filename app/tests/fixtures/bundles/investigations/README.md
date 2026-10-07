# Investigation test-pack fixtures

Original GP Connect integration responses retrieved on 29 September 2026 using only `includeInvestigations`. These are separate from the earlier allergy-only and legacy bundles; in particular, the existing `../9692136744.json` has not been replaced.

| Supplier | Patient | Fixture | Diagnostic reports | Full conversion |
| --- | --- | --- | ---: | --- |
| EMIS | Qiu Dai | [9730333939.json](9730333939.json) | 20 | HTTP 200 |
| EMIS | Georgina Mold | [9465700088.json](9465700088.json) | 34 | HTTP 200 |
| TPP | Louise Job | [9692136744.json](9692136744.json) | 32 | HTTP 200 |
| TPP | Bob Theresa | [9465699896.json](9465699896.json) | 5 | HTTP 500: FHIR parse failure |

All upstream requests returned HTTP 200. The Bob Theresa fixture deliberately retains the source's malformed `Specimen.note` object and three `DiagnosticReport.performer: [{}]` values. It is a negative parsing fixture, not a successful converter input. Do not normalise it silently or count it as five successfully converted reports.

The other three fixtures parse through `fhirclient.models.bundle.Bundle` and produce complete XML, but contain the content losses described in the assessment. Full conversion success does not imply clinical acceptance. Reports may contain source data different from the workbook; no fixture has been edited to match an expected result.

[manifest.json](manifest.json) records hashes, request IDs, counts and observed parser failures. Only response bodies and non-secret provenance are committed as fixture data; request headers, credentials and temporary logs are excluded.

See the [assessment](../../../../../docs/assurance/evidence/2026-09-29-investigations-assessment.md) and [per-report inventory](../../../../../docs/assurance/evidence/2026-09-29-investigations-report-inventory.csv). The two input workbooks are `GP Connect ARS Clinical Test Pack - EMIS sent to UCLH 23 Sept.xlsx` and the corresponding TPP workbook, sheet `07_Investigations`.
