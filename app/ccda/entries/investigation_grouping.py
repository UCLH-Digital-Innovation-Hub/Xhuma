"""Resolve GP Connect investigation membership without inferring groups from values."""

from collections import defaultdict
from dataclasses import dataclass

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


def group_investigation(report: DiagnosticReport, index: dict) -> InvestigationGrouping:
    """Follow both STU3 relationship directions within a report's context.

    The index must resolve the references supplied in the bundle, including
    fullUrl aliases when used. Object identity reconciles aliases without
    stripping URL prefixes and accidentally conflating different resources.
    """
    observations = {}
    identities = {}
    for reference, resource in index.items():
        if isinstance(resource, Observation):
            canonical = identities.setdefault(id(resource), reference)
            observations[canonical] = resource

    def resolve(reference):
        """Resolve a reference only when it targets an indexed Observation."""
        return identities.get(id(index.get(reference)))

    direct = []
    issues = []
    for reference in report.result or []:
        target = resolve(reference.reference)
        if target is None:
            issues.append(f"Missing report result: {reference.reference}")
        elif target not in direct:
            direct.append(target)

    other_roots = set()
    for resource in index.values():
        if isinstance(resource, DiagnosticReport) and resource is not report:
            other_roots.update(resolve(r.reference) for r in resource.result or [])
    other_roots.difference_update(direct)
    edges = []
    missing = defaultdict(list)
    for source, observation in observations.items():
        for relation in observation.related or []:
            if relation.type not in ("has-member", "derived-from"):
                continue
            reference = relation.target.reference if relation.target else None
            target = resolve(reference)
            if target is None:
                missing[source].append(reference)
            else:
                edges.append((source, target, relation.type))

    scope = dict.fromkeys(direct)
    changed = True
    while changed:
        changed = False
        for source, target, kind in edges:
            candidates = [target] if source in scope else []
            if kind == "derived-from" and target in scope:
                candidates.append(source)
            for candidate in candidates:
                if candidate not in scope and candidate not in other_roots:
                    scope[candidate] = None
                    changed = True

    members, comments = defaultdict(list), defaultdict(list)
    parents, attached = set(), set()
    for source in scope:
        for reference in missing[source]:
            issues.append(f"Missing linked observation: {source} -> {reference}")
    for source, target, kind in edges:
        if source not in scope or target not in scope:
            if source in scope and target in other_roots:
                issues.append(f"Link crosses report boundary: {source} -> {target}")
            continue
        # Normalise reciprocal relationships into one parent/child edge.
        # A result's derived-from points to its group; a filing comment's
        # derived-from points to the result or group it annotates.
        owner, child = (source, target) if kind == "has-member" else (target, source)
        if is_filing_comment(observations[child]) and not is_filing_comment(observations[owner]):
            if child not in comments[owner]:
                comments[owner].append(child)
            attached.add(child)
        elif not is_filing_comment(observations[owner]) and not is_filing_comment(observations[child]):
            if child not in members[owner]:
                members[owner].append(child)
            parents.add(child)
        else:
            issues.append(f"Unclear comment association: {source} -> {target}")

    # Source reference order is a display choice, never evidence of membership.
    return InvestigationGrouping(
        {k: observations[k] for k in scope}, direct, dict(members), dict(comments), parents, attached, issues
    )
