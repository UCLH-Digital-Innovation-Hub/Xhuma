"""Report headings and optional laboratory narrative from explicit source context."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fhirclient.models.organization import Organization

from ..models.datatypes import CD
from .investigation_grouping import has_result_value, is_filing_comment, observation_label
from .result_context import report_specimens
from .result_values import coded_value, concept_text

DISPLAY_TIMEZONE = ZoneInfo("Europe/London")


def column_widths(*widths: int) -> dict:
    """CDA narrative colgroup, placed before the table header."""
    return {"col": [{"@width": f"{width}%"} for width in widths]}


def display_time(value, *, preserve_seconds=False) -> str:
    """Use UK local time; optionally retain meaningful specimen subminute detail."""
    if value is None:
        return "Not supplied"
    source = value.as_json()
    if "T" in source:
        instant = datetime.fromisoformat(source)
        if instant.tzinfo is not None:
            instant = instant.astimezone(DISPLAY_TIMEZONE)
        display = instant.strftime("%d/%m/%Y %H:%M")
        seconds = re.search(r"T\d{2}:\d{2}:(\d{2})(\.\d+)?", source)
        if preserve_seconds and seconds and (seconds[1] != "00" or seconds[2]):
            display += instant.strftime(":%S") + (seconds[2] or "")
        return display + (instant.strftime(" %Z") if instant.tzinfo is not None else "")
    parts = source.split("-")
    return "/".join(reversed(parts))


def explicit_group_summary(grouping, title: str) -> str:
    """Summarise existing root memberships, never infer panels from prose."""

    def normalized(label):
        return " ".join(label.split()).casefold()

    seen = {
        normalized(title),
        "",
        "other results",
        "unplaced source items",
        "report-level filing",
        "comment note",
    }
    labels = []
    for root in grouping.roots:
        if not grouping.members.get(root):
            continue
        label = observation_label(grouping.observations[root])
        key = normalized(label)
        if key not in seen:
            seen.add(key)
            labels.append(label)
    summary = "; ".join(labels[:4])
    if len(labels) > 4:
        summary += f"; + {len(labels) - 4} more groups"
    return summary


def flat_header_summary(headers) -> str:
    """Use unvalued source headers for navigation only, without inferring membership.

    Free-text results can look like headers. Accept only empty commentary or
    supplier original-label metadata; leave other narrative results untouched.
    """
    labels = []
    seen = set()
    for header in headers:
        if has_result_value(header) or header.referenceRange or header.interpretation or header.dataAbsentReason:
            continue
        lines = (header.comment or "").splitlines()
        if any(line.strip() and not line.strip().casefold().startswith("original text:") for line in lines):
            continue
        if not header.code or not (header.code.text or any(c.display for c in header.code.coding or [])):
            continue
        label = observation_label(header)
        key = " ".join(label.split()).casefold()
        if key and key not in seen:
            seen.add(key)
            labels.append(label)
    summary = "; ".join(labels[:4])
    if len(labels) > 4:
        summary += f"; + {len(labels) - 4} more titles"
    return summary


def narrative_paragraphs(text: str) -> list:
    """Keep one source field in one paragraph, preserving every source line.

    Break-only nodes keep xmltodict pretty-print indentation out of source text
    nodes on both delivery paths. Each CRLF, CR or LF becomes exactly one
    lowercase CDA break, including leading, trailing and repeated breaks.
    """
    lines = re.split(r"\r\n|\r|\n", text)
    return [
        text
        if len(lines) == 1
        else {
            "content": [
                {"#text": lines[0]},
                *[node for line in lines[1:] for node in ({"br": None}, {"#text": line})],
            ]
        }
    ]


def report_temporal_context(report, index):
    """Share the heading's trusted collection interval or issued fallback.

    The boolean identifies collection provenance; no Observation time is used.
    """
    samples = report_specimens(report, index)
    intervals = []
    for sample in samples:
        collection = sample.collection
        if collection and collection.collectedDateTime:
            intervals.append((collection.collectedDateTime, collection.collectedDateTime))
        elif collection and collection.collectedPeriod:
            intervals.append((collection.collectedPeriod.start, collection.collectedPeriod.end))
        else:
            intervals.append((None, None))
    # Every reference must resolve, including aliases; unknown samples cannot
    # establish a shared collection time for the report.
    resolved = {id(s) for s in samples}
    complete = all(id(index.get(r.reference)) in resolved for r in report.specimen or [])
    signatures = [tuple(t.as_json() if t is not None else None for t in pair) for pair in intervals]
    if intervals and complete and len(set(signatures)) == 1 and all(intervals[0]):
        return *intervals[0], True
    return report.issued, report.issued, False


def report_heading_time(report, index) -> str:
    """Prefer an unambiguous collection time, otherwise use the issued time."""
    start, end, collected = report_temporal_context(report, index)
    if collected:
        if start.as_json() == end.as_json():
            return display_time(start)
        return f"Collected {display_time(start)} – {display_time(end)}"
    return display_time(start) if start else "Time not supplied"


def report_sort_key(report, index):
    """Newest first when used with reverse=True; stable ties, unknowns last.

    Collection periods sort by their start. Partial calendar dates use the
    beginning of their supplied precision solely as a navigation key; this does
    not add precision to narrative or structured clinical timestamps.
    """
    start, _, _ = report_temporal_context(report, index)
    if start is None:
        return False, datetime.min.replace(tzinfo=timezone.utc)
    source = start.as_json()
    if "T" in source:
        value = datetime.fromisoformat(source)
    else:
        parts = [int(part) for part in source.split("-")]
        value = datetime(*parts, *([1] * (3 - len(parts))))
    # Calendar-only dates (and any timezone-free source time) retain their UK
    # calendar position; offset-bearing instants are compared in UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=DISPLAY_TIMEZONE)
    return True, value.astimezone(timezone.utc)


def report_identity(report, grouping, fallback_header=None, *, allow_panel=True):
    """Prefer a resolved panel, then a named report; retain flat supplier captions.

    A legacy header is only a label fallback for generic reports, never evidence
    that its neighbouring observations form a BATTERY.
    """
    panel = (
        grouping.roots[0]
        if allow_panel and len(grouping.roots) == 1 and grouping.members.get(grouping.roots[0])
        else None
    )
    concept = report.code
    title = concept_text(concept) if concept else ""
    if panel:
        source = grouping.observations[panel]
        concept, title = source.code, observation_label(source)
    elif fallback_header and title.casefold() in (
        "",
        "diagnostic report",
        "diagnostic studies report",
        "laboratory report",
        "investigation result",
    ):
        concept, title = fallback_header.code, observation_label(fallback_header)
    title = title or "Diagnostic Report"
    code = coded_value(concept).value if concept else CD(nullFlavor="UNK")
    return title, code, panel


def laboratory_details_table(report, grouping, index) -> dict | None:
    """Show only explicitly referenced performer organisations and their scope.

    A practitioner, requesting practice or specimen custodian is not assumed
    to be the performing laboratory. Observation performers retain result labels.
    """
    organisations = {}

    def add(reference, scope):
        organisation = index.get(reference.reference) if reference else None
        if not isinstance(organisation, Organization):
            return
        _, scopes = organisations.setdefault(id(organisation), (organisation, []))
        if scope not in scopes:
            scopes.append(scope)

    for performer in report.performer or []:
        add(performer.actor, "Report")
    for observation in grouping.observations.values():
        if not is_filing_comment(observation):
            for reference in observation.performer or []:
                add(reference, observation_label(observation))
    rows = []
    for organisation, scopes in organisations.values():
        addresses = []
        for address in organisation.address or []:
            text = address.text or ", ".join(
                filter(
                    None,
                    [
                        *(address.line or []),
                        address.city,
                        address.district,
                        address.state,
                        address.postalCode,
                        address.country,
                    ],
                )
            )
            if text:
                addresses.append(text)
        contacts = [f"{c.system or 'Contact'}: {c.value}" for c in organisation.telecom or [] if c.value]
        rows.append(
            {
                "td": [
                    organisation.name or "Name not supplied",
                    "; ".join(scopes),
                    {"paragraph": addresses} if addresses else "",
                    {"paragraph": contacts} if contacts else "",
                ]
            }
        )
    if not rows:
        return None
    return {
        "caption": "Performing laboratory / organisation",
        "colgroup": column_widths(25, 25, 30, 20),
        "thead": {"tr": {"th": ["Organisation", "Applies to", "Address", "Contact"]}},
        "tbody": {"tr": rows},
    }
