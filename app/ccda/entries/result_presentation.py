"""Report headings and optional laboratory narrative from explicit source context."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fhirclient.models.organization import Organization

from ..models.datatypes import CD
from .investigation_grouping import is_filing_comment, observation_label
from .result_context import report_specimens
from .result_values import coded_value, concept_text

DISPLAY_TIMEZONE = ZoneInfo("Europe/London")


def column_widths(*widths: int) -> dict:
    """CDA narrative colgroup, placed before the table header."""
    return {"col": [{"@width": f"{width}%"} for width in widths]}


def display_time(value) -> str:
    """Use UK local time for instants, retaining partial-date precision."""
    if value is None:
        return "Not supplied"
    source = value.as_json()
    if "T" in source:
        instant = datetime.fromisoformat(source)
        if instant.tzinfo is not None:
            return instant.astimezone(DISPLAY_TIMEZONE).strftime("%d/%m/%Y %H:%M %Z")
        return instant.strftime("%d/%m/%Y %H:%M")
    parts = source.split("-")
    return "/".join(reversed(parts))


def report_heading_time(report, index) -> str:
    """Prefer an unambiguous collection time; label the issued-time fallback."""
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
        start, end = intervals[0]
        if signatures[0][0] == signatures[0][1]:
            return display_time(start)
        return f"Collected {display_time(start)} – {display_time(end)}"
    return f"Issued {display_time(report.issued)}" if report.issued else "Time not supplied"


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
