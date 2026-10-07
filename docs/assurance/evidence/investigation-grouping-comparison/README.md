# Investigation grouping comparison

These two CCDAs use the same saved EMIS Qiu Dai bundle (`9730333939.json`), containing 20 DiagnosticReports. No new GP Connect request was made. Application converter files are unchanged.

- [Existing converter](existing.xml)
- [Proposed grouping prototype](proposed-grouping.xml)
- [Per-report comparison and source hash](comparison.json)
- [Reproducible isolated generator](generate.py)

These files are the historical comparison reviewed before implementation. The generator depends on the converter revision at that time and now refuses to overwrite these artifacts. Running it against the updated converter would not reproduce the old baseline.

## Scope

The prototype reconciles `has-member` and `derived-from` relationships, separates filing comments from results, renders group captions and associated comments, and selects result components from the relationship graph. It preserves one organizer per source report. Structured result components remain flat within that organizer; the prototype does not introduce nested CDA organizers or claim to provide a complete structured mapping of group metadata.

The existing result serializer is reused unchanged. Date, status, value, reference-range and table-cell issues are deliberately retained. Existing report captions, organizer metadata and Epic category components are also retained, even where a later implementation should improve them. Group comments and interpretations are retained in narrative. This is a grouping comparison, not implementation of all proposed fixes or a complete filing-provenance mapping.

The existing file uses the current converter. Only the generated document effectiveTime is aligned between runs to remove an incidental clock difference. Each run parses a fresh copy of the same fixture.

## Useful examples

| Report ID | Comparison |
| --- | --- |
| `9F5631B5-67BE-4A36-9E4C-8A28742239FE` | Renal function, serum lipids and FBC appear as explicit groups; group headings no longer masquerade as individual result components. |
| `96B3D07D-39C1-43A7-A3E8-91214B66CC68` | Traversal includes the transfer-degraded CHOL/HDL group and its members. |
| `E7D45D09-D343-4CDA-AC8F-942324C8B402` | The degraded FBC header no longer prevents traversal to its child results. |
| `B3453B4C-ACF6-40AB-B4FB-D8B736A2C7CA` | The string-valued INR is retained as a result instead of being discarded as a header. |

Search these report IDs in the XML, or search `Test group:` in the proposed narrative. Component counts and group membership are recorded in comparison.json. Counts include existing Epic category components where present, so they are not pure analyte counts.

## Verification

Both XML documents parse successfully and retain all 20 reports. A structural comparison asserts that differences are confined to investigation narrative and organizer components. The prototype accounts for every Observation in each report's resolved scope and rejects unresolved linked Observations or membership cycles. No CDA schema/Schematron validation or Epic import was performed; XML parsing does not establish conformance.
