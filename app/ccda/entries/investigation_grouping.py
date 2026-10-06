"""Resolve GP Connect investigation membership without inferring groups from values."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from heapq import heappop, heappush
from types import MappingProxyType

from fhirclient.models.diagnosticreport import DiagnosticReport
from fhirclient.models.observation import Observation


def is_filing_comment(observation: Observation) -> bool:
    """Recognise the prescribed SNOMED comment code in investigation data."""
    codings = observation.code.coding or [] if observation.code else []
    return any(c.system == "http://snomed.info/sct" and c.code == "37331000000100" for c in codings)


def observation_label(observation: Observation) -> str:
    """Prefer original text, then SNOMED display as in the result converter."""
    code = observation.code
    if code:
        codings = sorted(code.coding or [], key=lambda c: c.system != "http://snomed.info/sct")
        return code.text or next((c.display for c in codings if c.display), None) or observation.id
    return observation.id


def has_result_value(observation: Observation) -> bool:
    """Detect any supplied FHIR value, including zero and false."""
    # Inspect typed FHIR fields without serialising/revalidating unrelated
    # mandatory fields, so a missing status can still map to an unknown status.
    return any(
        json_name.startswith("value") and getattr(observation, name) is not None
        for name, json_name, *_ in observation.elementProperties()
    )


@dataclass
class InvestigationGrouping:
    """Keep source observations, report order and deduplicated associations."""

    observations: dict[str, Observation]
    direct: list[str]
    members: dict[str, list[str]]
    comments: dict[str, list[str]]
    parents: set[str]
    attached_comments: set[str]
    issues: list[str]
    roots: tuple[str, ...]
    report_comments: tuple[str, ...]


@dataclass(frozen=True)
class ReportRoots:
    """Ordered entry points and unresolved references for one source report."""

    direct: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class Association:
    """One deduplicated downward link, retaining its first source edge position."""

    position: int
    child: str
    is_comment: bool


@dataclass(frozen=True)
class InvestigationGraph:
    """Read-only relationship snapshot, shared only within one bundle conversion.

    The resource objects are borrowed, not copied. Do not change their references
    or relationships after building the graph. Rendering state stays per report.
    """

    observations: Mapping[str, Observation]
    aliases: Mapping[str, str]
    reports: Mapping[int, ReportRoots]
    root_owners: Mapping[str, frozenset[int]]
    edges: tuple[tuple[str, str, str], ...]
    missing: Mapping[str, tuple[str | None, ...]]
    discovery: Mapping[str, tuple[int, ...]]
    outgoing: Mapping[str, tuple[int, ...]]
    associations: Mapping[str, tuple[Association, ...]]
    filing_comments: frozenset[str]
    unclear: frozenset[int]


def _report_roots(report: DiagnosticReport, aliases: Mapping[str, str]) -> ReportRoots:
    direct = {}
    issues = []
    for reference in report.result or []:
        target = aliases.get(reference.reference)
        if target is None:
            issues.append(f"Missing report result: {reference.reference}")
        else:
            direct.setdefault(target, None)
    return ReportRoots(tuple(direct), tuple(issues))


def build_investigation_graph(index: Mapping[str, object]) -> InvestigationGraph:
    """Prepare aliases, report ownership, traversal indexes and associations once.

    Object identity reconciles aliases without stripping URL prefixes and
    accidentally conflating distinct resources. All reports contribute boundary
    information, including reports outside the section being rendered.
    """
    observations = {}
    identities = {}
    aliases = {}
    reports = {}
    for reference, resource in index.items():
        if isinstance(resource, Observation):
            canonical = identities.setdefault(id(resource), reference)
            observations[canonical] = resource
            aliases[reference] = canonical
        elif isinstance(resource, DiagnosticReport):
            reports[id(resource)] = resource

    roots = {identity: _report_roots(report, aliases) for identity, report in reports.items()}
    owners = defaultdict(set)
    for identity, report_roots in roots.items():
        for reference in report_roots.direct:
            owners[reference].add(identity)

    edges = []
    missing = defaultdict(list)
    discovery, outgoing, associations = defaultdict(list), defaultdict(list), defaultdict(list)
    filing_comments = frozenset(key for key, observation in observations.items() if is_filing_comment(observation))
    associated = set()
    unclear = set()
    for source, observation in observations.items():
        for relation in observation.related or []:
            if relation.type not in ("has-member", "derived-from"):
                continue
            reference = relation.target.reference if relation.target else None
            target = aliases.get(reference)
            if target is None:
                missing[source].append(reference)
            else:
                position = len(edges)
                edges.append((source, target, relation.type))
                outgoing[source].append(position)
                discovery[source].append(position)
                if relation.type == "derived-from" and target != source:
                    discovery[target].append(position)
                owner, child = (source, target) if relation.type == "has-member" else (target, source)
                if owner in filing_comments:
                    unclear.add(position)
                elif (owner, child) not in associated:
                    associated.add((owner, child))
                    associations[owner].append(Association(position, child, child in filing_comments))

    return InvestigationGraph(
        observations=MappingProxyType(observations),
        aliases=MappingProxyType(aliases),
        reports=MappingProxyType(roots),
        root_owners=MappingProxyType({key: frozenset(value) for key, value in owners.items()}),
        edges=tuple(edges),
        missing=MappingProxyType({key: tuple(value) for key, value in missing.items()}),
        discovery=MappingProxyType({key: tuple(value) for key, value in discovery.items()}),
        outgoing=MappingProxyType({key: tuple(value) for key, value in outgoing.items()}),
        associations=MappingProxyType({key: tuple(value) for key, value in associations.items()}),
        filing_comments=filing_comments,
        unclear=frozenset(unclear),
    )


def group_investigation(
    report: DiagnosticReport, index: Mapping[str, object] | InvestigationGraph
) -> InvestigationGrouping:
    """Resolve one report using a shared graph, or a standalone resource index.

    Visit only reachable edges. An ordered queue preserves the former full-scan
    discovery order, including edges reached on subsequent passes. Associations
    are already normalised; scope, roots and mutable rendering state stay local.
    """
    graph = index if isinstance(index, InvestigationGraph) else build_investigation_graph(index)
    observations = graph.observations
    edges = graph.edges
    report_identity = id(report)
    roots = graph.reports.get(report_identity)
    if roots is None:
        # Standalone callers may supply a report that is not in their index.
        roots = _report_roots(report, graph.aliases)
    direct = list(roots.direct)
    direct_set = set(direct)
    issues = list(roots.issues)

    def belongs_to_other_report(reference):
        if reference in direct_set:
            return False
        owners = graph.root_owners.get(reference)
        return bool(owners) and (len(owners) > 1 or report_identity not in owners)

    scope = dict.fromkeys(direct)
    pending = []
    scheduled = set()

    def schedule(reference, scan_pass, cursor):
        for position in graph.discovery.get(reference, ()):
            if position not in scheduled:
                scheduled.add(position)
                # Earlier edges would have been revisited on the next full scan.
                heappush(pending, (scan_pass + (position <= cursor), position))

    for reference in direct:
        schedule(reference, 0, -1)
    while pending:
        scan_pass, position = heappop(pending)
        source, target, kind = edges[position]
        candidates = [target] if source in scope else []
        if kind == "derived-from" and target in scope:
            candidates.append(source)
        for candidate in candidates:
            if candidate not in scope and not belongs_to_other_report(candidate):
                scope[candidate] = None
                schedule(candidate, scan_pass, position)

    members, comments = defaultdict(list), defaultdict(list)
    parents, attached = set(), set()
    associations = []
    diagnostic_edges = []
    for source in scope:
        for reference in graph.missing.get(source, ()):
            issues.append(f"Missing linked observation: {source} -> {reference}")
        for association in graph.associations.get(source, ()):
            if association.child in scope:
                associations.append((association.position, source, association))
        for position in graph.outgoing.get(source, ()):
            target = edges[position][1]
            if target not in scope and belongs_to_other_report(target):
                diagnostic_edges.append((position, f"Link crosses report boundary: {source} -> {target}"))
            elif target in scope and position in graph.unclear:
                diagnostic_edges.append((position, f"Unclear comment association: {source} -> {target}"))
    for _, owner, association in sorted(associations):
        child = association.child
        if association.is_comment:
            comments[owner].append(child)
            attached.add(child)
        else:
            members[owner].append(child)
            parents.add(child)
    issues.extend(issue for _, issue in sorted(diagnostic_edges))

    # Source reference order is a display choice, never evidence of membership.
    return InvestigationGrouping(
        observations={k: observations[k] for k in scope},
        direct=direct,
        members=dict(members),
        comments=dict(comments),
        parents=parents,
        attached_comments=attached,
        issues=issues,
        roots=tuple(k for k in scope if k not in parents and k not in graph.filing_comments),
        report_comments=tuple(k for k in direct if k in graph.filing_comments and k not in attached),
    )
