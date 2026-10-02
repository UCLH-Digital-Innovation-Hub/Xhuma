# How investigation results are grouped

## Purpose

A laboratory report can contain several groups of tests, individual results and comments added when a clinician files the report. We want the summary to preserve those relationships without losing results that arrive in an unusual format.

**We identify a test group from the links supplied by the GP system. A test name, an empty value or its position in a list is not enough to establish a group.**

This document describes the implemented grouping behaviour. It follows the [GP Connect investigations guidance, version 1.6.2](https://simplifier.net/guide/gp-connect-access-record-structured/Home/Design/Investigations-guidance?version=1.6.2) and the linked [Observation population guidance](https://simplifier.net/guide/gpconnect-data-model/Home/FHIR-Assets/All-assets/Profiles/Profile--CareConnect-GPC-Observation-1?version=current). The latter link is maintained as the current profile, rather than a fixed 1.6.2 snapshot.

## What arrives from the GP system

| Clinical concept | Name in the incoming FHIR data | Meaning |
| --- | --- | --- |
| Report | `DiagnosticReport` | The report that holds the results together |
| Test group | `Observation` | A panel or profile, such as full blood count |
| Individual result | `Observation` | An individual test, whether grouped or standalone |
| Filing comment | `Observation` | Information recorded when a report, group or result is filed |
| Specimen | `Specimen` | Information about the sample |
| Request summary | `ProcedureRequest` | Summary of the original request |

Group headings, results and filing comments all arrive as Observations. Their shared name does not mean they have the same clinical role.

```mermaid
flowchart TD
    list["Investigations list"] --> report["Laboratory report"]
    report --> request["Request summary"]
    report --> specimen["Specimen"]
    report --> group["Test group: e.g. full blood count"]
    report --> standalone["Standalone result"]
    report --> reportComment["Report filing comments"]
    group -->|has-member: contains| result["Individual test result"]
    result -->|derived-from: belongs to| group
    groupComment["Group filing comments"] -->|derived-from: comments on| group
    resultComment["Result filing comments"] -->|derived-from: comments on| result
    standaloneComment["Standalone filing comments"] -->|derived-from: comments on| standalone
```

The arrows describe relationships, not the order of items in the incoming message. The original report document and supporting images are outside the scope of this GP Connect version. Patient and clinician attribution are omitted from the diagram for readability.

## The decision process

1. **Keep each report separate.** Reports are not merged because they share a laboratory number or date.
2. **Read the supplied links.** A group can point to its results, and a result can point back to its group. Either direction provides evidence of membership.
3. **Separate filing comments from tests.** A comment linked to a result does not turn that result into a panel.
4. **Display the group and its members together.** Keep group comments with the group and result comments with the result. Show report-level filing comments separately.
5. **Keep ungrouped content.** If there is no evidence of membership, retain the observation without assigning nearby results to it.
6. **Show available information when links are broken.** Do not silently discard a report because some relationships cannot be resolved.

Source reference order is retained where possible, but grouping brings related items together. No clinical relationship is inferred from bundle entry order, matching timestamps or similar names.

## Reading the examples

The examples below are shortened, invented FHIR fragments. They show only the fields needed to explain grouping and are not complete patient records. Names such as `fbc` and `hb` are local labels used to link items together.

- `has-member` means “contains this result”.
- `derived-from` means “belongs to this group” for a result, or “comments on this item” for a filing comment in this GP Connect model.
- `reference` identifies the other item. It is not a clinical value.

### A panel with individual results

The FBC heading points to haemoglobin. Haemoglobin points back to FBC.

```json
[
  {
    "resourceType": "Observation",
    "id": "fbc",
    "code": {"text": "Full blood count"},
    "related": [{"type": "has-member", "target": {"reference": "Observation/hb"}}]
  },
  {
    "resourceType": "Observation",
    "id": "hb",
    "code": {"text": "Haemoglobin"},
    "valueQuantity": {"value": 137, "unit": "g/L"},
    "related": [{"type": "derived-from", "target": {"reference": "Observation/fbc"}}]
  }
]
```

**Summary behaviour:** display “Test group: Full blood count”, followed by haemoglobin. The two arrows describe one relationship; they do not produce two haemoglobin results. If only one direction is present, the available link is still used.

The same approach handles multiple panels in one report. Each gets its own heading and linked results. The report itself remains one report.

### A result with an attached filing comment

Here the comment points to haemoglobin. The comment code identifies its role; it is not another blood test.

```json
{
  "resourceType": "Observation",
  "id": "filing-note",
  "code": {
    "coding": [{"system": "http://snomed.info/sct", "code": "37331000000100"}]
  },
  "comment": "Discussed with patient; repeat as planned.",
  "related": [{"type": "derived-from", "target": {"reference": "Observation/hb"}}]
}
```

**Summary behaviour:** place the comment with haemoglobin. Do not create a group simply because a result has a comment attached. Some supplier messages instead link from the result to the comment using `has-member`; this association is also recognised.

A comment directly referenced by the report, with no result/group association, is displayed as report-level filing information. A filing comment can contain a coded value identifying the test that was filed: that value must not be treated as a new analyte result. This grouping change does not add a complete mapping of filing dates, authors or coded filing values.

### A standalone result expressed in words

```json
{
  "resourceType": "Observation",
  "id": "inr",
  "code": {"text": "International normalised ratio"},
  "valueString": "Ap 2.400",
  "comment": "Treatment bands supplied by the laboratory."
}
```

**Summary behaviour:** retain the result and its comment. A result does not have to contain a numeric quantity to count as a result. In the saved EMIS example, the old heading rule dropped this INR result; relationship-based grouping retains it.

A result can also exist entirely in a comment:

```json
{
  "resourceType": "Observation",
  "id": "oestradiol",
  "code": {"text": "Oestradiol"},
  "comment": "Result 8.8; units and reference information supplied in source text."
}
```

The text is retained. The converter does not extract a new numeric measurement from this prose.

### A report supplied as a flat list

```json
{
  "resourceType": "DiagnosticReport",
  "id": "report-1",
  "result": [
    {"reference": "Observation/chemistry-heading"},
    {"reference": "Observation/sodium"},
    {"reference": "Observation/potassium"}
  ]
}
```

If these Observations contain no membership links, the list alone does not establish that sodium and potassium belong to the apparent chemistry heading.

**Summary behaviour:** retain all three observations in the supplied report order. Do not invent a group. This matters for the saved TPP data, where many reports are flat. A familiar panel name is not enough to reconstruct the original layout confidently.

### A transferred record with a degraded heading

Records transferred between systems may have a generic transfer code while preserving the original heading as text.

```json
{
  "resourceType": "Observation",
  "id": "transferred-fbc",
  "code": {
    "coding": [{"system": "http://snomed.info/sct", "code": "196411000000103"}],
    "text": "Full blood count - FBC"
  },
  "related": [{"type": "has-member", "target": {"reference": "Observation/hb"}}]
}
```

**Summary behaviour:** use the supplied text for the group heading and follow its result links. Individual transfer-degraded results also retain their original names in the result rows and in the structured code’s `originalText`; the supplied code and its coded display remain unchanged. The transfer code does not prevent grouping. The saved EMIS FBC example demonstrates why this matters: skipping a degraded heading can hide its child results.

## Unusual or incomplete relationships

| Situation | Summary behaviour |
| --- | --- |
| A group contains another group | Follow the links recursively and show both headings with their results |
| The same result belongs to two groups | Show the result once within the report and an “Also associated” note at the other location |
| A group unexpectedly contains its own value | Keep the heading, members and the supplied value through the existing value converter |
| A link points to an unavailable observation | Show available content and a notice that some relationships could not be resolved; do not invent the missing result |
| Links form a loop | Stop following the loop, show available items once and display a grouping notice |
| An observation cannot be placed under a normal heading | Retain it under “Unplaced source item” |
| A link would pull in an item directly assigned only to another report | Keep the reports separate and flag the unresolved association |

Zero and false count as supplied values when deciding whether a group has content to retain. This does not mean every FHIR value type has a completed CDA mapping; value conversion is a separate concern.

## What this change does and does not establish

The readable CCDA section now shows the detected groups and associated comments. Each source report still produces one CCDA organizer (the structured container for that report). Individual result components remain flat inside it: this change does not introduce nested CDA organizers or claim that Epic will reconstruct every displayed group from structured data alone.

The earlier report-caption and category-selection rules remain for this change. A caption may therefore be less descriptive than the group headings beneath it. Result rows use four fixed columns: component, value, reference range and comments. Headings and annotations span all four columns. Reference ranges use typed wrappers with one observation range per source range; source bounds retain their own units. The date mapping is explained below. Result value mapping is described below. Report conclusions now appear above the report’s results table. Notes from specimens referenced by that report appear in a separate “Specimen notes” table beneath it, labelled with specimen type and identifier where supplied. Multiple notes and their line breaks are retained. These conclusions and specimen notes are narrative additions, not invented test results. Provider List.note warnings, including incomplete-record-transfer notices, appear above the investigation reports, including when the list is empty. Other previously identified metadata gaps remain separate work.

The [comparison CCDAs](assurance/evidence/investigation-grouping-comparison/README.md) show the reviewed before-and-after behaviour. The [assessment](assurance/evidence/2026-09-29-investigations-assessment.md) records the original findings and wider converter issues; it is historical evidence rather than a description of the current grouping code.

For clinical review, check that group headings describe their linked tests, comments remain with the correct result or group, transferred headings retain their children, and flat reports do not imply unsupported relationships. A successful XML conversion alone does not confirm clinical correctness or Epic display behaviour.

## Laboratory status mapping

The structured CCDA status describes laboratory progress, not whether the GP has reviewed or filed the report. This mapping does not filter out non-final reports or change the separate Epic finalised-results policy.

| Incoming FHIR status | CDA status |
| --- | --- |
| `registered`, `partial`, `preliminary` | `active` |
| `final`, `amended`, `corrected`, `appended` | `completed` |
| `cancelled` | `aborted` |
| `entered-in-error` | Organizer: `nullified`; individual result: `nullFlavor="OTH"`, with the original status displayed in its comments and structured text |
| `unknown` or missing | `nullFlavor="UNK"`, without a code |
| An unrecognised value | `nullFlavor="OTH"`; individual result status retained in comments and structured text |

The mappings are implementation choices based on the [FHIR STU3 report status definitions](https://hl7.org/fhir/STU3/codesystem-diagnostic-report-status.html) and [CDA result status guidance](https://hl7.org/fhir/us/ccda/en/ConceptMap-CF-ResultStatus.html). FHIR cancellation does not distinguish cancellation before work starts from abandonment after work starts; `aborted` is used conservatively. Corrections and amendments occur after finalisation. The restricted CDA Result Status vocabulary does not include `nullified`, so a withdrawn individual result uses an explicit non-mappable status and visible source wording. Unknown is never converted to completed.

## Result values

The value converter produces structured CDA and the matching “Value” cell together. It keeps supplied numbers and words; it does not extract a new measurement from a comment.

| Source value | Structured CDA | Readable result |
| --- | --- | --- |
| Quantity | `PQ` | Source number and unit wording |
| Quantity with a comparator | `IVL_PQ` | Source comparator, number and unit wording |
| String | `ST` text content | Original text, including line breaks |
| Coded concept | `CD`, original text and additional codings where representable | Original text or supplied display/code |
| Boolean | `BL` | True or False, including a supplied false |
| Integer, if exposed by the input model | `INT` | Integer, including zero; the current STU3 Observation model does not accept `valueInteger` in incoming JSON |
| Range | `IVL_PQ` | Supplied bounds and their units |
| Ratio | `RTO_PQ_PQ` | Numerator and denominator, with their respective units |

For upper-bound quantities, the agreed zero lower-bound convention is retained. Strict `<` and `>` bounds explicitly use `inclusive="false"`; `<=` and `>=` use `inclusive="true"`. If zero would create an inverted or empty interval (for example `< -5` or `< 0`), the original quantity is retained as labelled unmapped text for review. Actual FHIR Range values retain only their supplied bounds. These choices follow the [HL7 quantity mapping guidance](https://build.fhir.org/ig/HL7/ccda-on-fhir/en/mappingGuidance.html#ranges-of-physical-quantities).

A supplied UCUM code is used for the structured unit while the source wording remains in the table. Where UCUM is not declared, only a small set of recognised unit spellings is accepted directly. Other units, or missing units, retain the magnitude and source wording in a CDA quantity translation. This is deliberately not a general unit-conversion service and does not assume a missing unit means dimensionless.

Known coding systems and explicit OIDs are retained as CDA codes. Codes without a known OID are preserved as labelled source text in translations rather than inventing a coding system. Text-only concepts use an explicit null flavour and original text.

Where a data-absent reason is supplied, the readable result explains it and the CDA carries a corresponding null flavour. A missing quantity is not zero. A comment-only observation without a separate value remains comment-only. Unsupported value types (including attachments, sampled data and date/time values) and malformed ratios are retained as labelled source text and logged for review, rather than decoded or discarded. This preserves content but does not promise that Epic can process it as that original datatype. An empty string is explicitly shown as such.


## Specimens, authors and dates

The structured report can contain several specimens and several authors. Specimens retain their source identifiers and coded sample type, such as serum. Supporting several authors in the model does not make a laboratory performer an author: authorship still requires source evidence of that role.

Section 7 of *Requirements to Enable Happy Together Labs for Other Vendors* gives the two result timestamps different meanings:

| CCDA location | Meaning for Epic | Source used |
| --- | --- | --- |
| Report organizer effective time | Specimen collection time | The referenced specimen's collected date/time or collection period |
| Each result observation's effective time | The report's finalising instant, shared by all components | DiagnosticReport.issued, as the available report issue-time proxy |
| Author time | When the author authored the information | Authorship information, when supplied; this is separate from both laboratory timestamps |

An issue timestamp is not proof that a report is final. This mapping does not introduce a new finalised-results filter. Laboratory receipt time and GP filing time are not substituted for collection or finalisation.

The organizer time has both a lower and an upper bound. For a single collection instant they are equal. For a collection period they retain its start and end; an absent endpoint is explicitly unknown. If several specimens share the same collection interval, that interval can be used. If their collection times differ, a specimen cannot be resolved, or no collection time is supplied, the organizer has unknown bounds rather than an arbitrarily selected date. Individual collection and receipt dates remain visible in the specimen table beneath the results, alongside the specimen notes.

Dates retain their supplied precision and timezone. A year-only date stays a year; it is not turned into 1 January. Missing report issue time stays unknown for every component rather than borrowing an individual test's date.

This follows Epic's collection-time interpretation of the organizer. The general [C-CDA Result Organizer definition](https://build.fhir.org/ig/HL7/CDA-ccda-2.1-sd/StructureDefinition-ResultOrganizer.html) describes its effective time as spanning its component observations instead. The model comments make this distinction explicit so that the Epic mapping is not accidentally replaced by a different interpretation.


## Reference ranges and malformed responses

Each source reference range has its own structured CDA wrapper. Numeric bounds retain their own units and zero values; absent endpoints are omitted. Text-only ranges have a string value. Source text, range type, population and age qualifiers remain in the range narrative. Units missing from a range are not copied from the measured result. Unrecognised units are retained using the same translation handling as result quantities.

Numeric abnormal highlighting requires one unqualified range and known matching units for the result and the compared boundary. Multiple ranges, age/population/type qualifiers, missing units and mismatched units do not trigger inferred highlighting. No unit conversion is attempted. Source interpretation codes and comments remain available independently.

Malformed GP Connect JSON or FHIR bundles return HTTP 502 with `success: false` and an error beginning `FHIR bundle malformed:`, followed by the validation error. The existing application-failure telemetry records the exception and the audit trail records a failed `validate_fhir_bundle` event with the request correlation and error details. The upstream HTTP success audit remains distinct from this validation failure. Failed validation does not proceed to conversion or caching, and audit-persistence failures still propagate. Supplier data is not repaired silently.


## Performance and scaling considerations

These measurements describe the grouping implementation reviewed on 1 October 2026 and the subsequent shared-graph change. They are engineering observations, not clinical acceptance evidence. Graph preparation is shared across reports. The indexed traversal measurements below record the subsequent optimization.

### Baseline: repeated graph preparation

Before the shared-graph change, [`group_investigation()`](../app/ccda/entries/investigation_grouping.py) rebuilt its observation identity map, other-report roots and relationship edges for every diagnostic report. Each call scanned the bundle index, including aliases, and the observations' `has-member` and `derived-from` relationships. Scope expansion repeatedly scanned all edges until no more observations entered the report, followed by another edge scan to build groups and comments. The shared-graph implementation retains those traversal scans to preserve ordering, while removing the repeated preparation.

For R reports, N index entries, D report-result references scanned through the index and E observation relationships, the baseline repeated preparation was approximately O(R × (N + D + E)). Scope expansion adds full-edge passes whose number depends on link depth and source ordering. List membership checks used for deduplication can add further cost for large panels. Doubling the number of reports and observations can therefore require substantially more than twice the grouping work.

A local experiment used independent in-memory copies of the existing `investigations/9465700088.json` fixture, with resource IDs, fullUrl aliases and references rewritten for each copy. Each bundle was parsed and indexed before timing all its reports through `group_investigation()`. These are medians of five runs on Python 3.14.7; they exclude FHIR parsing, result rendering, XML serialization and external I/O.

| Synthetic fixture copies | Resources | Reports | Grouping time |
| --- | ---: | ---: | ---: |
| 1 | 452 | 34 | 17 ms |
| 2 | 904 | 68 | 69 ms |
| 4 | 1,808 | 136 | 309 ms |
| 8 | 3,616 | 272 | 1,464 ms |

The timings demonstrate superlinear growth in this experiment. They are not production throughput estimates: the container uses Python 3.13, and representative supplier workloads and deployment hardware must be measured separately.

### Implemented shared investigation graph

[`build_investigation_graph(index)`](../app/ccda/entries/investigation_grouping.py) now prepares an `InvestigationGraph` from the complete bundle reference index. [`convert_bundle()`](../app/ccda/fhir2ccda.py) builds it on reaching the first non-empty investigation section, then passes the same graph to every report and any subsequent investigation sections. Bundles with no investigation entries do not build a graph.

The graph contains:

- Canonical observation references and alias resolution based on resource identity. Distinct resources remain distinct even when URL suffixes or resource IDs match.
- Each unique report's ordered, deduplicated direct observations and unresolved direct references. Report aliases are deduplicated by identity.
- Observation-to-report ownership. Boundary checks use this map instead of rescanning other reports or rebuilding an exclusion set. An observation directly referenced by the current report remains eligible even if other reports also reference it. All indexed reports contribute ownership, including reports outside the rendered section.
- Relationship edges in their original order, with the source, target and relationship type retained.
- Missing targets grouped by source observation, so unrelated damage does not leak into a report's warnings.

The graph's containers are read-only mappings, tuples and frozen sets. The FHIR resources themselves are borrowed rather than copied: references and relationships must not change after graph construction. Per-report scope, direct-reference lists, warnings and rendering state are newly allocated, so one report cannot change another's grouping state. The graph is local to one conversion and is not stored in a process-global cache.

`investigation(report, index, graph)` uses the resource index for other metadata and the graph for grouping. Existing two-argument `investigation(report, index)` calls remain supported and build a graph for that standalone call. Likewise, `group_investigation(report, index)` remains supported alongside `group_investigation(report, graph)`. Callers converting several reports should build and pass a shared graph to receive the performance benefit. Standalone reports absent from the resource index can still resolve their direct references using the graph's aliases.

### Measured effect of sharing graph preparation

The same synthetic fixture procedure was rerun against the original grouping function and the shared-graph implementation in one local session. These are medians of five runs on Python 3.14.7. The shared time includes graph construction once plus grouping every report; both columns exclude parsing, rendering, XML serialization and external I/O.

| Resources | Reports | Original grouping | Shared graph plus grouping | Graph construction alone |
| --- | ---: | ---: | ---: | ---: |
| 452 | 34 | 16.14 ms | 4.85 ms | 0.50 ms |
| 904 | 68 | 66.53 ms | 16.85 ms | 1.06 ms |
| 1,808 | 136 | 309.04 ms | 62.45 ms | 2.07 ms |
| 3,616 | 272 | 1,401.48 ms | 250.22 ms | 4.66 ms |

Grouping was approximately 3.3–5.6 times faster in this experiment. These are grouping improvements, not equivalent end-to-end conversion speedups. At that stage, growth remained superlinear because each report still scanned the complete ordered edge sequence. The largest graph retained approximately 607 KiB of additional traced Python allocations, with approximately 981 KiB peak allocations during construction, excluding already parsed resources; these figures are not process RSS.

Before/after checks compared every grouping field, mapping order, structured organizer and narrative table across 86 reports from the three valid investigation fixtures. The intentionally malformed fixture was excluded at FHIR parsing. An additional 1,200 randomized report groupings matched the original implementation, covering aliases, overlapping roots, missing links and cycles. Persistent regression tests cover report-state isolation, shared direct observations, distinct resources with matching IDs, existing edge discovery order, standalone callers, and one graph build across multiple non-empty investigation sections.

### Implemented indexed traversal

The graph builder now also prepares ordered discovery adjacency, source-edge adjacency for diagnostics, filing-comment classification and deduplicated downward associations. Each association records its owner, child, whether it is a comment and its first source edge position. Reciprocal `has-member`/`derived-from` links are normalised once. Read-only containers and frozen association records retain the per-conversion lifetime.

All direct report references seed one ordered work queue. `has-member` permits source-to-target discovery; `derived-from` permits discovery from either endpoint. Each reachable source edge is scheduled at most once, and report ownership is checked before accepting a candidate. The queue uses `(scan pass, source edge position)` priorities to reproduce the former repeated-scan discovery order without scanning unrelated edges. This deliberately preserves display order rather than introducing a new breadth-first ordering policy.

Once scope is known, only its indexed associations and outgoing diagnostic edges are examined. Associations are filtered to the report's scope and sorted by their original positions; missing-link and boundary diagnostics retain their former order. The resulting report view includes explicit display `roots` and unattached `report_comments`. Rendering starts from these roots and follows members/comments downward, retaining its shared-result notes and cyclic/unplaced-item fallback. A report reference to a child can still discover its parent and siblings during scope resolution; a downward-only discovery walk would lose those observations.

Shared preparation remains O(N + D + E). For a report with Vᵣ reachable observations and Eᵣ incident relationships, resolution visits local adjacency rather than the whole bundle. Queue and association ordering add up to O(Eᵣ log Eᵣ) work; root selection is O(Vᵣ). Shared observations may legitimately be visited in several reports. No ancestor/descendant transitive closure or process-global report cache is introduced. Standalone callers still build a graph per call, so callers processing multiple reports should pass the shared graph.

### Measured effect of indexed traversal

The [raw A/B measurements](investigation-indexed-benchmark-2026-10-01.json) compare the working-tree shared-graph implementation immediately before this change with indexed traversal. The baseline source hash is recorded because it included earlier uncommitted work. These are medians of nine alternating A/B runs, after warm-up, on Python 3.14.7. Independent copies of the 452-resource fixture have IDs and references rewritten before parsing. Timings include one shared graph build plus grouping all reports, and exclude parsing, rendering, serialization and external I/O. Individual stage medians need not sum to the median total.

| Resources | Reports | Before: shared graph + grouping | After: indexed graph + grouping | Speedup |
| --- | ---: | ---: | ---: | ---: |
| 452 | 34 | 7.103 ms | 3.061 ms | 2.32× |
| 904 | 68 | 17.503 ms | 4.454 ms | 3.93× |
| 1,808 | 136 | 69.273 ms | 10.568 ms | 6.55× |
| 3,616 | 272 | 256.693 ms | 19.022 ms | 13.49× |

At 3,616 resources, graph construction increased from 4.172 ms to 11.629 ms while grouping decreased from 251.657 ms to 7.431 ms. The tradeoff is additional indexes: retained graph allocations increased from 606.90 KiB to 1,390.56 KiB, and construction peak from 981.23 KiB to 2,424.23 KiB. Allocation measurements use a separate `tracemalloc` run and exclude existing parsed resources; they are not process RSS. These are grouping improvements, not measured end-to-end request speedups.

A separate 15-run check of the flat, zero-edge fixture (244 resources, 32 reports) increased shared build plus grouping from 0.463 ms to 0.822 ms. With no relationship scans to eliminate, classification and explicit-root preparation add about 0.36 ms; some of that work previously happened in the renderer, which these timings exclude. The raw measurements include this case.

Reproduce using an archived copy of the pre-change grouping module:

```bash
.venv/bin/python -m scripts.benchmark_investigation_grouping \
  --baseline /tmp/xhuma-investigation-before/investigation_grouping.py \
  --output /tmp/investigation-indexed-benchmark.json
```

The baseline path is a local session snapshot, not a repository dependency. For another comparison, archive the desired baseline module before editing and pass that path. The benchmark checks grouping equivalence before timing and supports `--fixture`, `--copies` and `--repeat`.

### Content and ordering validation

Before/after comparisons matched every existing grouping field and dictionary order, plus complete structured organizers and narrative tables, across all 86 reports in the three valid investigation fixtures. An additional 4,000 deterministic randomized report groupings matched the baseline, including overlapping roots, aliases, forward/reverse links, duplicates, comments, missing links and cycles. The malformed fixture remains excluded at FHIR parsing.

Persistent regressions additionally cover child-only entry points resolving parents and siblings, multiple seed ordering, immutable deduplicated associations, a 1,500-link reverse-ordered chain and a guard that rejects full-edge iteration while 1,000 unrelated edges are present. Existing tests retain coverage for shared results, report boundaries, missing results, zero/false values, valued groups and rendering-state isolation. The renderer remains recursive; extremely deep rendered chains still warrant a separate iterative-rendering change.

### Async execution and validation

Investigation grouping and result rendering perform CPU work without network I/O. Their async wrappers do not make that work parallel. Adding `asyncio.gather()` around reports would not remove repeated scans or provide CPU parallelism on the normal event loop. In a separate local experiment, four concurrent full conversions of the 452-resource fixture produced a roughly 1.1-second gap in a heartbeat scheduled every 5 ms. That measurement includes parsing, rendering and serialization and must not be attributed to grouping alone.

Continue reducing repeated traversal work first. If large conversions still delay requests, evaluate a bounded process pool or separate conversion workers, including queue limits, serialization costs, cancellation behavior and the deployment's relay constraints. Avoid creating an unbounded task or process for every report.

Separate timings now cover index construction, shared graph preparation, per-report grouping, post-grouping result rendering and XML/base64 serialization, alongside an event-loop delay probe. See [the updated pipeline profile and monitoring guide](benchmarking.md#reprofile-1-october-2026) for measurements, instrument names and a repeatable offline profiling command. The reprofile still found an approximately 865 ms heartbeat gap during four concurrent conversions of the 452-resource fixture.

Continue validating on supplier fixtures and synthetic bundles with increasing report count, relationship depth, alias count and shared observations. Track event-loop delay, peak memory and output equivalence alongside latency; fixed wall-clock thresholds in ordinary unit tests would be unreliable.
