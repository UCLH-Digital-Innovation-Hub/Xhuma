# GP Connect investigations assessment — 29 September 2026

## Outcome

The current implementation does **not yet satisfy the investigation test-pack expectations**, even where it successfully generates a CCDA. The most significant failures are loss of narrative results and report conclusions, incorrect grouping, loss of specimen/date information, and inconsistent table columns.

Four fresh investigation-only bundles were retrieved through the existing audited GP Connect implementation. Three complete CCDAs were generated, containing 86 diagnostic reports. The fourth bundle contains five reports but fails FHIR parsing before `results.py` runs. All four original response bodies are now [repository fixtures](../../../app/tests/fixtures/bundles/investigations/README.md).

No application code, existing tests or previous fixtures were changed. Temporary assessment tooling was run outside the repository. This is a source-data and converter assessment, not an Epic rendering/import test or clinical sign-off.

## Scope and evidence

Both supplied workbooks were assessed using their `07_Investigations` sheets. Their contents were treated as test expectations, not operational instructions. The embedded images behind Excel `#VALUE!` cells were extracted; screenshot text was read with OCR and selected images were visually checked. Exact numerical claims below are corroborated by worksheet text or a readable screenshot; the workbook remains authoritative where screenshot text is ambiguous.

The EMIS sheet says one patient and 32 reports, but introduces a second patient at row 238 for case 7.8. Both patients were included. TPP has two patients. EMIS screenshots are in B40, B72, B99, B134, B156, B192 and B245; TPP result screenshots are in F217, F220, F225, F230, F236, F278 and F346.

Calls used the NHS integration environment, requesting organisation RRV00, ASID 200000002574, and only the `includeInvestigations` parameter, without a date filter. Normal auditing remained enabled. The existing local PostgreSQL and Redis containers were started for retrieval and stopped afterwards. Credentials and request headers are excluded from repository evidence.

| Supplier / test patient | NHS number | Incoming reports / observations / specimens | Application outcome |
| --- | --- | --- | --- |
| EMIS — Qiu Dai | 9730333939 | 20 / 153 / 22 | HTTP 200; complete XML generated |
| EMIS — Georgina Mold | 9465700088 | 34 / 301 / 37 | HTTP 200; complete XML generated |
| TPP — Louise Job | 9692136744 | 32 / 206 / 0 | HTTP 200; complete XML generated |
| TPP — Bob Theresa | 9465699896 | 5 / 14 / 5 | Upstream HTTP 200; application HTTP 500 during FHIR parsing |

The [manifest](../../../app/tests/fixtures/bundles/investigations/manifest.json) records checksums and audit request IDs. The [91-row report inventory](2026-09-29-investigations-report-inventory.csv) records source IDs, dates, conclusions, specimen references, generated titles, oversized table rows and observed text losses. The existing `app/tests/test_results.py` suite passes: **16 tests, 19 warnings**. Those tests verify selected current behaviour; passing them does not establish the workbook requirements.

## Workbook versus incoming bundle versus output

“Partial” means useful content survives but the entire scenario cannot pass. “Fail” identifies a demonstrated converter loss. “Blocked” identifies a failure before conversion or unavailable source evidence. Related cross-cutting defects are detailed below.

### EMIS

| Case and worksheet evidence | Incoming bundle compared with workbook | Current CCDA / assessment |
| --- | --- | --- |
| **7.1 Full blood count** — B40, D67 | Matching lab ID `CHL1020170307041006H,03.9110541001` is present. Numeric results include Hb 13.7 g/dL, WBC 5.7, platelets 293, MCV 88.3, and the uncoded/degraded values. Specimen taken 3 July 2023 15:14 and received 20 January 2024 16:27 match the screenshot. `Infection` is supplied in `DiagnosticReport.conclusion`; GP annotations are separate linked comments. | **Partial/fail overall.** Many values survive, but `Infection` is omitted, comments are flattened and the report gets the generic title `Diagnostic Report`. Specimen details and dates are unused. Degraded original descriptions in `code.text` are not carried through. |
| **7.2 Urea/electrolytes, lipids, thyroid** — B72, D94 | Matching lab ID `CHL0321310503160601N,05.0050728001`; sodium 136, potassium 4.5, urea 4.2, creatinine 93, cholesterol 6.3, HDL 1.3, ratio 4.8 and TSH 1.4 are supplied. The original CHOL/HDL RATIO description is in degraded `code.text`. `No clinical details given` is a report conclusion. | **Partial.** Values/ranges are largely retained. The report is titled only `Urea and electrolytes`; original degraded title and conclusion are lost. Group membership and report/specimen metadata are not preserved as requested. |
| **7.3 Glucose tolerance** — B99, D129 | Three source DiagnosticReports share lab ID `CHM0100950302121100G,03.0999008001`: one effectively empty comment report and two with narrative results. The latter contain fasting 4.3 and two-hour 7.6, interpretive bands and the named classification comment. | **Partial/fail overall.** FT1–FTX4 degraded comments survive as text, but original names in `code.text` are lost and the glucose group-header classification comment is discarded. An abnormal GP comment survives in one report, but loses its original group association. Monospaced layout is not established. The multiple source reports are not merged. |
| **7.4 Limited title / INR** — B134, D152 | Lab ID `1013/HA2101103U/202303301621`; source result is `valueString="Ap 2.400"`, with the treatment-band comment. `ON WARFARIN` is the report conclusion. The screenshot foregrounds the treatment bands; the extra value is source-supplied, not invented by Xhuma. | **Fail.** INR is misclassified as the sole group header. Its value and treatment-band comment are both dropped. `ON WARFARIN` is also dropped. Only the title/category and an empty comment row remain. |
| **7.5 Multiple reports with same lab ID** — B156, D187 | The intended result asks for independent reports. This bundle supplies **one** DiagnosticReport for `3060261772-020603173610-1`, with renal, lipid, fasting glucose and FBC content and multiple specimen references. This differs from the requested separate-report structure before conversion. | **Source limitation plus converter shortfall.** Xhuma cannot claim to display separately received reports when only one is supplied. It then flattens that report's groups and omits specimens. This is not evidence that Xhuma merged several incoming DiagnosticReports. |
| **7.6 Comment-only COELIAC** — B192, D202 | Report `461839A4-9B28-46D4-BC27-297C46F4F80E`, lab ID `1019/HA3701201C/202303301627`, has conclusion `COELIAC`. Its comment Observation has no text. Specimen collection date is absent, matching “Not Specified”; specimen type and received time are present. | **Fail.** The report remains present but its clinically relevant text disappears, leaving a generic title and empty comment row. Available specimen metadata and explicit missing-collection-time presentation are absent. |
| **7.7 Unfiled reports** — D233 | No specific unfiled report is identifiable from the sheet's text/image-free scenario block or the returned Qiu records. All 20 source DiagnosticReports have status `unknown`; this is not evidence of GP filing or review. | **Not demonstrated.** No filed/unfiled summary is generated. Do not label the scenario passed merely because no unfiled marker was observed. Provider exposure and a known unfiled example need confirmation. |
| **7.8 Complex report, Georgina Mold** — B245, D265 | Report `EEB34DD8-B080-4602-89E7-ED8B678079F1`, lab ID `15/CH000063K/200010191704`, contains the expected microscopy, organisms and aligned antibiotic sensitivity text in Observation `0BDD0472-91C2-4ED1-81CC-640476125063.comment`. `Otitis media` is the report conclusion. | **Fail.** The narrative observation is classified as a group header and removed from the result rows/components. The sensitivity report is lost, not merely reformatted; the conclusion is also absent. |

### TPP

| Case and worksheet evidence | Incoming bundle compared with workbook | Current CCDA / assessment |
| --- | --- | --- |
| **7.1 Full blood count** — rows 38–128 | Report `c200000000000000_a437000000000000` matches the lab ID and expected values. Hb is already supplied as 137 g/L, with the original 13.7 g/dL conversion note. Some numeric results, including RDW 12.7 and degraded entries, are supplied in comments rather than `valueQuantity`. | **Partial.** Expected values/comments largely survive in this multi-header case, including mononucleosis and morphology text. Group structure is flattened, original formatting is not assured, and dates/filing metadata do not meet the complete scenario. The g/L conversion occurred upstream; it is not performed by `results.py`. |
| **7.2 Grouped chemistry/lipids/thyroid** — rows 132–188 | Report `c200000000000000_4237000000000000` contains the expected numeric values, ratio 4.8 and transfer-degraded CHOL/HDL text. Specimen description/ID and report title are embedded in filing comments; there are no Specimen resources in Louise's bundle. | **Partial.** Content largely survives, but the caption names only the first group. Filing comments are appended below results rather than presented as prominent report metadata, and group associations are flattened. Missing primary specimen timestamps cannot be recovered from this bundle. |
| **7.3 Glucose tolerance** — rows 192–242 | Reports ending `_3437000000000000` and `_2437000000000000` match the lab ID. Fasting 4.3, two-hour 7.6, bands and degraded FT1–FTX4 text match the supplied screenshots. One report also contains the abnormal/contact-patient header comment. | **Partial/fail overall.** FT comments survive, but both glucose header comments are discarded, including classification/GP action wording in the applicable report. Plain text alone does not establish preserved monospaced alignment. |
| **7.4 Limited title / INR** — rows 249–286 | Report ending `_c337000000000000` contains numeric INR 2.4, treatment bands, `ON WARFARIN` as an Investigation result string, and report metadata in a filing comment. This differs from EMIS's `valueString` and report-conclusion representation. | **Partial.** INR, bands and `ON WARFARIN` survive here. The caption is nevertheless the invented placeholder `Diagnostic Report`, which the intended result prohibits. Metadata remains undifferentiated footer text and the table has excess cells. |
| **7.5 Same lab ID, separate reports** — rows 293–451 | Three distinct DiagnosticReports ending `_4437000000000000`, `_5437000000000000` and `_6437000000000000` share the textual lab ID `3060261772-020603173610-1`, with Serum, Fluor and EDTA details in comments. | **Pass for retaining three records; partial overall.** Xhuma produces three separate organizers/tables without overwriting them. Header/group structure, timestamps and complete metadata presentation still fall short. Independent UI selection/auditing is outside this converter check. |
| **7.6 Comment-only ON AZATHIOPRINE** — rows 458–485 | Report ending `_d337000000000000` contains the expected narrative and specimen/report details in its supplied comments/results. It also contains an added `NHS E Test Case 7.6` note not shown in the workbook. | **Partial.** The narrative survives and the report is not hidden. A generic title is added; report metadata and preserved layout remain incomplete. The additional test-case note is a source difference, not an application addition. |
| **7.7 Long lab comment / oestradiol** — rows 492–543 | Report ending `_f237000000000000` contains multi-specimen details in its filing comment. The oestradiol Observation carries `Value: 8.8 pmol/L`, low interpretation and range 10–90 as comment text, not a numeric value. | **Fail.** That observation becomes the sole group header and its result comment is discarded. The long filing/specimen comment survives, but that does not compensate for loss of the actual result. There is no length truncation in the converter; classification is the demonstrated cause. |
| **7.8 Unfiled, Bob Theresa** — rows 553–556 | All five returned DiagnosticReports say `final`. The three named tests are present, with filing/action comments and dates differing from the 2011 arrival date shown in the workbook. A separate glucose report explicitly says it was automatically filed and awaits review. Thus the current source snapshot does not represent three simply “unfiled” reports as described. | **Blocked by parsing**, plus a source-state mismatch. `Specimen.note` is an object; three report performers are empty objects missing `actor`. Nothing from this bundle reaches the full CCDA. A review-required source message must not be confused with a laboratory `final` status if parsing is later addressed. |

## Confirmed implementation shortfalls

### 1. Narrative result loss caused by header detection — highest priority

[`is_test_group_header`](../../../app/ccda/entries/results.py) checks only `valueQuantity` and two special codes. A `valueString`, a coded non-numeric result, or a result held entirely in `comment` can therefore be treated as a header. When exactly one top-level header is found, the later filter removes every observation classified as a header.

This explains the demonstrable losses in EMIS INR, Georgina's microbiology report and TPP oestradiol. Multiple-header reports take a different branch and retain these same kinds of observations, so survival depends on surrounding report structure. The existing glucose test even expects only an Epic category component for a report whose source has a substantial narrative comment; it codifies current behaviour rather than complete preservation.

Relevant locations: `results.py:53–66`, `273–289`, `291–328`. Header/member handling should eventually distinguish actual group relationships from the presence or absence of a numeric value, and retain header-level clinical content.

### 2. Report conclusions and comment hierarchy

`investigation()` never reads `DiagnosticReport.conclusion`. All 26 nonempty conclusions across the two EMIS bundles are absent from their corresponding generated report output, including `COELIAC`, `ON WARFARIN`, `Infection` and `Otitis media`.

Comment observations are appended as unlabelled narrative rows and omitted from structured components. Only one level of membership is expanded, and only from initially recognised headers. Nested GP comments can therefore be missed; retained comments lose their report/group/result placement and author/date context. EMIS's two glucose classification header comments are dropped even when other linked GP comments survive.

Relevant locations: `results.py:266–363`, especially `275–289` and `349–353`. The eventual mapping needs separate report, group and result comments, not a single flattened tail of notes.

### 3. Specimens and dates

`diagnostic_report.specimen` is never used. EMIS supplies collection times, received times, specimen types, accession identifiers and multiple specimens; these are absent from output. Louise's TPP bundle supplies no Specimen resources, so details available only in narrative can be retained but unavailable timestamps must not be invented.

Every component receives the report-issued interval, overriding its own `Observation.effectiveDateTime`. The shared `datetime_helper` also strips time and timezone and produces midnight. For example, TPP `2024-01-20T16:27:00+00:00` becomes `20240120000000` structurally. The caption retains the Python-rendered issued timestamp, creating a narrative/structured discrepancy. This cannot meet the requirement to distinguish taken, received and issued times.

Relevant locations: `results.py:99–105`, `272`, `334–343`; `app/ccda/helpers.py:106–112`.

### 4. Table shape and clinical interpretation

The row starts with four empty cells, then uses `insert()` for description, value, comment and range. Ordinary rows consequently have five to eight cells beneath four headers. Across the three successful bundles there are **453 such oversized rows** (97 Qiu, 205 Georgina, 151 Louise). Comments can move into a fifth column after the reference range is inserted. This is present in generated XML, not a speculative browser issue.

Narrative abnormal emphasis is calculated from numeric reference ranges; supplied interpretation codes are not consistently rendered as explicit abnormal text. Numeric highlighting alone does not meet the workbook's requirement for correct result/group/report indicators independent of colour. Actual Epic styling has not been retested.

Relevant locations: `results.py:107–114`, `116–204`, `206–260`.

### 5. Original text, report titles and monospaced formatting

The result label uses coded display rather than preserving `CodeableConcept.text`. For EMIS transfer-degraded entries this can turn a supplied original test name into the generic “Transfer-degraded record entry”. TPP often avoids that particular loss because its original name is already repeated in `comment`.

Zero or multiple detected headers cause the invented `Diagnostic Report` caption; one header causes the report to be named after only that group. Neither respects all the supplied titles, and the limited-title scenarios explicitly prohibit fabricated placeholders.

Retained comments are emitted as ordinary narrative cell content or strings. No explicit preformatted/monospaced representation is generated. Raw newline/space retention is not proof that a receiver renders aligned lab text correctly. [HL7 narrative guidance](https://www.hl7.org/cda/stds/core/2.0.0-sd/narrative.html) distinguishes text from rendering hints; receiver verification is still necessary.

### 6. Filing/review state and completeness warnings

The converter does not display report status as filing/review state. In these fixtures FHIR statuses are `unknown` (EMIS) or `final` (TPP), neither of which alone proves GP review. Some TPP review information exists in filing-comment prose; it is not a consistent summary-level status. Patient-facing access is outside `results.py` and was not assessed.

A separate section-level gap is that the investigations branch in `fhir2ccda.py` does not append `List.note`. Georgina's provider warning about incomplete transfer before 15 December 2020 is therefore lost, despite being supplied in the bundle. This affects confidence in completeness and needs reporting to the clinician preparing a summary.

### 7. Structured CDA defects and untested edge cases

- `final` is converted to **`Completed`**, whereas the CDA code is lowercase **`completed`**. See the [HL7 Result Observation example](https://cdasearch.hl7.org/examples/view/Guide%20Examples/Result%20Observation%20%28V3%29_2.16.840.1.113883.10.20.22.4.2).
- String values use `{"@value": text}` without a concrete datatype or ST text content. Such values can survive dictionary/XML generation without being a correct typed CDA string.
- Reference ranges are assigned as a dictionary despite a list-typed model; multiple `observationRange` elements can occur in one wrapper, and text-only ranges have no value. These are already noted as TODOs and generate serialization warnings. [HL7 Result Observation definitions](https://www.hl7.org/cda/us/ccda/StructureDefinition-ResultObservation-definitions.html) require one observationRange per referenceRange and a value within each observationRange.
- The extra Epic category observation omits required result fields and the dated result template. Its conformance concerns are already documented in source TODOs. Epic's acceptance of its local code has not been tested here.
- Comparator handling assumes a lower bound of zero for `<` results and omits explicit exclusive bounds for strict `<`/`>` values. That can change the source meaning; it requires dedicated tests before claiming coverage. This is a code-review finding, not the cause of the named scenario failures.
- `valueCodeableConcept` and other value types have no result-value mapping. Missing `issued`, identifiers, coding arrays or references can raise exceptions; there is no per-report containment in the section generator. These are code-review risks beyond the successfully converted snapshots.

No complete CDA schema/Schematron validation was run. XML parsing and Pydantic serialization are not equivalent to conformance or Epic import validation.

## What already works

The three parseable bundles all generate complete documents and retain one organizer/table per incoming DiagnosticReport: 20, 34 and 32 respectively. Distinct TPP reports sharing a lab ID remain separate. Many quantitative values, units, numeric ranges and result comments survive; platelet 497 against 150–450 is highlighted. Transfer-degraded TPP narrative and comment-only `ON AZATHIOPRINE` are retained in the applicable reports. These useful behaviours should be retained when addressing the failures.

## Suggested next work — not implemented

1. Resolve the TPP source-shape/parser compatibility questions using the preserved negative fixture, without silently rewriting provider evidence.
2. Fix result/header classification and retain conclusions, original text and comments at their proper levels. Use INR, COELIAC, microbiology and oestradiol as regression cases.
3. Correct four-column row construction; preserve plain-text layout and explicitly display supplied abnormal indicators.
4. Map specimen and distinct timestamps, report titles, review/filing messages and provider completeness warnings, marking unavailable metadata honestly.
5. Correct structured datatypes, status codes and reference ranges, then validate against the applicable CDA templates and Epic.

For the clinical validation summary, keep supplier-data discrepancies separate from Xhuma losses: 20 versus 32 EMIS reports, combined versus separate report structure, source-specific unit/text representations, missing TPP specimen metadata, and changed filing state are not all converter defects.
