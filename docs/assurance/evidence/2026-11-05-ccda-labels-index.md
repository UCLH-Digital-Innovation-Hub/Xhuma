# C-CDA labels and investigation index — incremental evidence

## Baseline and scope

Fresh isolated branch `fix/ccda-labels-investigation-index` was created after `git fetch origin`, from `7394b7907e3a6481059be37398eba13de9ea31ad` (`origin/dev`). This already includes #349's presentation polish and #350's marker-only Xhuma emphasis. Neither is reapplied here. This is a new independently revertible PR, not an addition to the older combined/revert PRs.

Production edits are limited to `entries/medication.py`, `entries/result_presentation.py`, `entries/results.py` and `fhir2ccda.py`. Requests, parsing, validation, clinical inclusion, mapping, grouping, result/range association, abnormality logic, SOAP/XDS, cache, audit and infrastructure are untouched. The deliberate structured-document difference is **report organizer order**, aligned with the reordered narrative; each organizer's content is unchanged.

## Medication labels

Bold labels with ordinary values now cover structurally known Instructions, Patient Instructions, Medication status reason, Last issued date and Issued quantity. Known source prefixes are limited to exactly:

- `Prescriber Notes:`
- `Patient Notes:`
- `Additional Information:`
- `Transfer degraded medication text:`

Matching is case-sensitive and anchored at the beginning. There is no generic colon parser. Supplier `Xhuma:`, embedded labels, or supplier text beginning with a structural-only label remain ordinary. Existing first-occurrence/deduplication behaviour is unchanged. Generated Xhuma notes retain marker-only emphasis. Agency and repeat statements remain ordinary and keep their wording.

An explicit normal-value text node begins with a literal separator space, independent of pretty-print whitespace. Source remainders are retained verbatim, including pre-existing leading whitespace; ordinary narrative rendering collapses that whitespace to a visual separator. Structured source text is never normalized. Newly generated labels, instruction continuations and prescription separators use real lowercase `br` elements on both serialized transports. Instructions have no leading space before their first label, and empty instruction groups do not add blank lines.

Actual delivered example, with surrounding indentation omitted:

```xml
<content>
  <content styleCode="Bold">Patient Notes:</content>
  <content> issue number 2 Adding message when prescribing 2nd time</content>
  <br/>
</content>
```

Its structured comment retains the original `Patient Notes:issue number 2 Adding message when prescribing 2nd time`, without the narrative-only added space.

## Investigation chronology

`report_temporal_context` exposes the selection previously embedded in `report_heading_time`; heading selection remains the same. A complete, shared collection interval is trusted only when all specimen references resolve and all supplied intervals agree. Otherwise the report uses its issued time. Missing/ambiguous collection context never selects an arbitrary specimen or Observation time.

- Sort descending by the selected structured temporal value, not formatted text.
- Collection periods use their **start** as the ordering key.
- Offset-bearing instants are compared in UTC; equal instants retain source order.
- Calendar-only partial dates use the start of their supplied precision in UK local time **for ordering only**. This deterministic convention cannot resolve uncertainty between overlapping partial dates; it does not add clinical precision to displayed or structured values.
- No-time reports come last, preserving their source order.
- One sorted reference sequence drives TOC items, each item's body and corresponding section organizer entries. No deduplication, membership changes or source-list mutation occurs.

## Contents summaries and navigation

Summaries use only the already-computed grouping's **explicit roots with members**. Standalone result names, nearby rows and comment prose are never promoted to groups. Title duplicates, duplicate group names and the four generic labels (Other results, Unplaced source items, Report-level filing, Comment note) are excluded with whitespace/case normalization for comparison only. Output preserves original group labels. At most four useful distinct labels appear, followed by a neutral `+ N more groups` count; actual body content is never truncated.

The existing plain-caption TOC mechanism is retained, using the permitted parenthetical fallback rather than relying on unverified multiline Epic caption behaviour. Fixture examples:

```text
Issued 20/02/2011 15:53 GMT — Diagnostic studies report (Autoimmune profile)
03/07/2023 15:14 BST — Diagnostic studies report (Full blood count - FBC)
```

The report title/code selection itself is unchanged. No report-level normal/abnormal status is inferred. A single panel already named by the report title is not repeated in a summary.

`list styleCode="TOC"`, item-contained bodies, native captions and all existing table widths are preserved. Xhuma still creates no explicit narrative navigation IDs or custom links; Epic generates links and Back to top. The optional separate body title/time was deliberately not added because the same caption drives the working navigation mechanism. **Actual Epic links, duplicate-caption navigation, Back to top, and visual spacing need receiver verification.** Local XML tests do not certify them.

## Report prose and specimen times

Interpretation and filing remain distinct paragraphs with separate Bold labels. Every supplied CRLF, CR or LF now becomes one real lowercase narrative break, including leading/trailing/repeated source blank lines. Source text and break-only nodes are separate so production pretty-printing cannot insert indentation into the source lines themselves. No `Title:`/`Specimen ID:` field parser was introduced. No invented blank paragraphs, repeated layout breaks, spacer table rows or new styles were added. Existing result-group, specimen and laboratory table boundaries/captions remain intact.

Specimen collection and receipt use UK display formatting; missing period endpoints remain explicitly unknown. Year/month/day precision is retained. Meaningful seconds and supplied fractional seconds are preserved in specimen detail, so same-minute period bounds do not become indistinguishable. Existing report-heading formatting remains unchanged. Examples:

```text
2023-07-03T15:14:00+01:00 → 03/07/2023 15:14 BST
2024-01-20T16:27:00+00:00 → 20/01/2024 16:27 GMT
2024-07-20T10:46:37.125Z → 20/07/2024 11:46:37.125 BST
2024-02 → 02/2024
```

## Serialized checks and adversarial review

Tests exercise `base64_xml` and actual `convert_mime` payloads, not just dictionaries. Coverage includes all medication labels, arbitrary-colon exclusions, source-Xhuma exclusions, exact source remainders, multi-dose ordering, plain structured comments, exact filing line text in delivered XML, source blank lines, specimen seconds/fractions/partial dates, missing bounds, stable report ordering, ambiguous/unresolved specimens, summary filtering/limits and preserved table widths/abnormal values.

An independent review identified three issues during development: pretty-print whitespace entering filing lines, escaped new medication breaks on MIME, and omitted specimen seconds. All three were fixed within narrative construction/display and independently reverified: **159 targeted tests passed**, plus direct production-serialization probes. No remaining blocker was found in that review.

**Pre-existing limitation:** ordinary medication note and Xhuma-warning tails still depend on the existing base64 break-unescaping behaviour; MIME retains their escaped break strings. This PR fixes real elements for newly introduced markup but does not broaden scope to the legacy serializer or note model. No new blanket supplier-text unescaping was added.

### Whole-entry comparison

Offline conversions used baseline `7394b79` and this branch, with identical deterministic terminology stubs and no live clinical/terminology services. Six full documents came from the existing fixture bundles `9690937472`, `9692136744`, the three investigation bundles (`9465700088`, `9692136744`, `9730333939`), and existing medication/allergy test builders composed with label/newline examples.

All **241 structured entries** matched as a multiset, ignoring only the intentionally changed outer report order, element-tail indentation at section boundaries, and generated UUID roots. Entries were not deduplicated in the comparison. Separately, existing exact SHA-256 regressions for **86 supplier investigation organizers** passed without normalization. Medication-focused development comparisons also matched all six whole-entry scenarios against the baseline generator. Local generated XML/logs are under `/tmp/xhuma-ccda-labels-index-evidence/`; no full fixture documents are added to the PR.

### Final verification

- `uv run --locked pytest -q`: **1,375 passed, 1 skipped, 14 subtests passed**, 46 warnings.
- `uv run --locked ruff check .`: passed.
- `uv run --locked ruff format --check .`: passed.
- `git diff --check`: passed.

Existing pytest configuration/collection and asyncio fixture-scope notices, Redis/Starlette deprecations, and Pydantic serialization warnings remain. New fixture cases exercise existing Pydantic warnings. The local unavailable telemetry collector also logs export failures, as on the earlier baseline. No unrelated implementation changes were made to silence these.

The previously documented 24-hour narrative freshness / approximately 4-hour runtime cache discrepancy remains untouched and still requires deliberate clinical/safety interpretation.
