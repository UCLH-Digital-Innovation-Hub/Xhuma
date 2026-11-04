# C-CDA clinical presentation evidence — 4 November 2026

Baseline: `aa9b04e` (`origin/dev` at worktree creation). Presentation only; this is not Epic acceptance or clinical sign-off.

## Scope and review

Production changes are confined to narrative construction in `fhir2ccda.py` and the medication, allergy, problem, immunisation and results entry renderers. No FHIR requests, inclusion rules, parsing, validation, mappings, lookup behaviour, grouping, range association, abnormality calculation, structured-entry construction, SOAP/XDS, audit, cache or infrastructure code changed.

- Ordinary section notices lose `flagData`. Conversion-failure warnings remain fully visible in Bold; supplier completeness/confidentiality notes remain ordinary.
- The completed-medication/future-end-date discrepancy retains its existing date and explanation; the date uses Bold rather than red. Selection and splitting logic is unchanged.
- A final production-code search finds exactly one `flagData` use: the result-value cell guarded by the unchanged `outside_reference_range(observation)` predicate. Normal and boundary values remain ordinary; no whole rows or reports acquire abnormal labels.
- Medication, allergy, problem and vaccine primary names use Bold. Dates, statuses and source notes do not acquire blanket styling. Vaccine notes are separate ordinary content. Allergy annotation line splitting is unchanged, including CR/LF variants and the original structured annotation text.
- All requested clinical-table widths total 100%; active, past and fallback medication layouts are identical. Result widths are 28/17/20/35.
- Following clinical feedback, only the Xhuma provenance marker uses Bold: `Xhuma:` for standard generated notes, and `Xhuma` for event-timing warnings. Explanatory text remains normal weight. Formatting is assigned at the code generation point. There is no supplier-prefix/colon parser. Existing first-occurrence/deduplication behaviour is retained: if supplier text exactly collides with a generated warning, that first supplier occurrence remains ordinary. Structured warning text and ordering are unchanged.
- Important Information retains every original sentence, scope/omission meaning and freshness value, separated into paragraphs with Bold labels. The generator statement is visible narrative rather than a footnote.
- Report-level filing uses a Bold heading and separate paragraphs for each structured comment label/value/comment; source text is not parsed, truncated or deduplicated.

## Navigation and receiver boundary

The existing `list styleCode="TOC"` contains one item per rendered report, in the existing order. Each report's caption, paragraphs and tables remain inside that item. There are no explicit narrative IDs, custom links or back-to-top elements in Xhuma's source XML: Epic generates these from the TOC. No new IDs or navigation mechanism were added. Duplicate captions and repeated references remain separate items.

Count and navigation guidance precede the list. Captions now put the existing `report_heading_time(...)` output first, retaining its issued fallback label, date precision and GMT/BST. Existing report identity logic is unchanged; supplier `Title:` prose is not promoted into identity. Native report captions and existing Bold table-group captions are retained rather than introducing a second heading/navigation mechanism. Numeric dates are deliberately retained to avoid a second timestamp formatter.

Repository inspection found offline profiling tooling but no established local Epic-equivalent browser rendering workflow. No renderer was built. **Epic screenshots, individual links and Back to top still require receiver verification**; XML tests establish preservation of the working navigation contract, not receiver behaviour.

## Serialized evidence

The real `base64_xml` delivery encoder was used, not only dictionary assertions. Investigation tests also exercise `convert_mime`. Regression coverage lives in:

- `test_section_presentation_xml.py`: full-document notices, populated/empty/fallback layouts, sums, Important Information with all/none/mixed scope and default/overridden freshness.
- `test_meds.py`: marker-only Bold with ordinary explanatory text for dm+d, route, free-text, multiple-dosage and event warnings; supplier spoof/collision cases; plain structured comments; lowercase narrative breaks.
- `test_allergy_narrative.py`: supplier newline variants and original structured text.
- `test_clinical_name_presentation_xml.py`: primary anchors with ordinary metadata, and structured XML snapshots (generated UUID roots normalised).
- `test_investigation_presentation_xml.py`: singular/40-item TOCs, duplicate identities/captions, named/generic reports across years, preserved order, distinct filing paragraphs, abnormal/normal/boundary values and exact pre-change organizer hashes.

An additional offline before/after conversion used an archive of `aa9b04e` and the same deterministic dm+d stub on both sides. No live clinical or terminology services were queried. Full documents were generated from checked-in fixtures:

| Fixture under `app/tests/fixtures/bundles/` | Structured section entries |
| --- | ---: |
| `9690937472.json` | 27 |
| `9692136744.json` | 124 |
| `investigations/9465700088.json` | 34 |
| `investigations/9692136744.json` | 32 |
| `investigations/9730333939.json` | 20 |
| Existing medication/allergy Python fixture builders, composed to cover all warning types and CR/LF notes | 4 |
| **Total** | **241** |

All 241 serialized structured entries matched, normalising UUID root values only. Separately, regression tests compare all 86 supplier investigation organizers against exact pre-change XML SHA-256 hashes, without normalization. The 34- and 20-report documents retained respectively 21 and 9 flagged values; the 32-report fixture retained zero. These are existing value-level results, not report-level interpretations.

Local full XML and run logs are in `/tmp/xhuma-ccda-presentation-evidence/`; these temporary artifacts are not committed or used as production data. Concise excerpts below omit surrounding document markup and whitespace, not clinical text within the shown fragments.

### Medication warning

Before:
```xml
<content><content styleCode="Bold">Xhuma:</content> Dose of 50 mg automatically mapped via dm+d lookup <br/></content>
```
After clinical-feedback amendment (standard note emphasis matches the baseline; event warnings now also carry a provenance cue):
```xml
<content><content styleCode="Bold">Xhuma:</content> Dose of 50 mg automatically mapped via dm+d lookup <br/></content>
<content><content styleCode="Bold">Xhuma</content> warning: Event-based medication timing: Morning (MORN). <br/></content>
```
The matching structured `xmlText` still contains plain warning text, with newlines and no `content`/`br` children.

### Investigation index and filing

Before:
```xml
<caption>Diagnostic studies report (Issued 30/03/2023 00:00 BST)</caption>
<list listType="unordered">
  <caption styleCode="Bold">Report-level filing</caption>
  <item><paragraph><content styleCode="Bold">Comment note</content></paragraph></item>
</list>
```
The above illustrates the old wrapper; its source prose is omitted from this excerpt only. After, the wrapper is replaced with paragraphs, preserving the actual source prose:
```xml
<paragraph>32 investigation reports</paragraph>
<paragraph>Select a report below to jump to its results.</paragraph>
<list styleCode="TOC">
  <item>
    <caption>Issued 30/03/2023 00:00 BST — Diagnostic studies report</caption>
    <paragraph><content styleCode="Bold">Report-level filing</content></paragraph>
    <paragraph><content styleCode="Bold">Comment note</content></paragraph>
    <paragraph>Title: Immunoglobulin E level
Result indicator: Unknown
Message: Report ID: 1013/CH2101117I/202303301621
Specimen ID: CH2101117I
Specimen description: SERUM
Patient Informed Details: Patient does not need to be informed
Follow Up Action: Other</paragraph>
  </item>
</list>
```
Result tables and remaining report items are omitted from this excerpt only. The generic structured title is deliberately not replaced by the supplier's prose title.

## Known clinical/safety follow-up

`CCDA_CACHING_PERIOD` still defaults to **24 hours** in the narrative. Runtime document expiry uses **`CCDA_EXPIRY_HOURS`**, configured at approximately **4 hours** in PLAY/INT/PRD. This PR does not reconcile them. The freshness statement must not be assumed to be merely a stale cache setting: deliberate clinical/safety interpretation is required.

## Validation and existing warnings

Final verification (rerun after the marker-only emphasis amendment):

- `uv run --locked pytest -q`: **1,316 passed, 1 skipped, 14 subtests passed**, 42 warnings.
- Focused medication/warning/medication-section amendment tests: **34 passed**.
- Regenerated the six fixture documents after the amendment: all **241 structured entries** still match the preceding PR version (UUID roots normalised). Serialized warnings contain only `Xhuma:` or `Xhuma` inside Bold; their explanatory text is an ordinary tail before lowercase `<br/>`.
- `uv run --locked ruff check .`: passed.
- `uv run --locked ruff format --check .`: passed.
- `git diff --check`: passed.

The baseline suite passed: **1,287 tests + 14 subtests, 1 skipped**, with 36 warnings. The change suite adds serialization regressions. Existing warnings include pytest configuration/collection and asyncio fixture-scope notices, Redis/Starlette deprecations and Pydantic serialization warnings. Additional fixture cases exercise those existing Pydantic warnings; no structured models were changed to silence them. Both baseline and changed runs also log failed telemetry exports to the unavailable local `otel-collector:4317`. These did not fail tests and were not changed in this scope.
