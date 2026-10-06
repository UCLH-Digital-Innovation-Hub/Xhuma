"""Generate grouping-only comparison artifacts; never patch application files."""

import asyncio
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

# Allow direct execution from the evidence directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import xmltodict
from fhirclient.models.bundle import Bundle
from fhirclient.models.observation import Observation

from app.ccda import fhir2ccda
from app.ccda.entries import results
from app.ccda.helpers import datetime_helper
from app.ccda.models.datatypes import IVL_TS, IVXB_TS

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
FIXTURE = ROOT / "app/tests/fixtures/bundles/investigations/9730333939.json"
AUDIT = []


def key(resource):
    """Return a stable resource key within this bundle."""
    return f"{resource.resource_type}/{resource.id}"


def is_comment(observation):
    """Identify the GP Connect investigation filing-comment code."""
    return any(
        c.system == "http://snomed.info/sct" and c.code == "37331000000100"
        for c in (observation.code.coding or [] if observation.code else [])
    )


def label(observation):
    """Prefer original text, then a supplied coding display, then resource ID."""
    code = observation.code
    return (
        (code.text or next((c.display for c in code.coding or [] if c.display), None)) if code else None
    ) or observation.id


def full_row(text):
    """Represent a group caption or annotation across the existing four columns."""
    return {"td": [{"@colspan": 4, "#text": text}]}


async def prototype(report, index):
    """Change membership and placement only; reuse existing result serialization."""
    baseline = await results.investigation(report, index)
    all_obs = {key(o): o for o in index.values() if isinstance(o, Observation)}
    edges = []
    for source, observation in all_obs.items():
        for relation in observation.related or []:
            if relation.type not in ("has-member", "derived-from"):
                continue
            reference = relation.target.reference if relation.target else None
            target = index.get(reference)
            if not isinstance(target, Observation):
                raise ValueError(f"Unresolved observation link: {source} -> {reference}")
            edges.append((source, key(target), relation.type))

    direct = [key(index[r.reference]) for r in report.result or []]
    scope = dict.fromkeys(direct)
    # Do not let reverse lookup traverse observations owned directly by a
    # different report unless this report also references them explicitly.
    other_roots = {
        key(index[r.reference])
        for resource in {key(o): o for o in index.values()}.values()
        if resource.resource_type == "DiagnosticReport" and resource.id != report.id
        for r in resource.result or []
    } - set(direct)
    changed = True
    while changed:
        changed = False
        for source, target, kind in edges:
            additions = []
            if source in scope:
                additions.append(target)
            if kind == "derived-from" and target in scope:
                additions.append(source)
            for candidate in additions:
                if candidate not in scope and candidate not in other_roots:
                    scope[candidate] = None
                    changed = True

    members, comments = defaultdict(list), defaultdict(list)
    parents, attached = set(), set()
    for source, target, kind in edges:
        if source not in scope or target not in scope:
            continue
        if kind == "has-member":
            owner, child = source, target
        else:
            owner, child = target, source
        if is_comment(all_obs[child]) and not is_comment(all_obs[owner]):
            if child not in comments[owner]:
                comments[owner].append(child)
            attached.add(child)
        elif not is_comment(all_obs[owner]) and not is_comment(all_obs[child]):
            if child not in members[owner]:
                members[owner].append(child)
            parents.add(child)

    rows, components, emitted = [], [], set()
    group_time = IVL_TS(low=IVXB_TS(value=datetime_helper(report.issued)))
    groups, result_keys = [], []

    async def render(resource_key, path=()):
        """Render groups recursively, retaining content and detecting real cycles."""
        if resource_key in path:
            raise ValueError(f"Membership cycle: {path + (resource_key,)}")
        if resource_key in emitted:
            rows.append(full_row(f"Also associated: {label(all_obs[resource_key])} (shown above)"))
            return
        emitted.add(resource_key)
        observation = all_obs[resource_key]
        if is_comment(observation):
            rows.append(full_row(f"Filing comments: {label(observation)}"))
            if observation.valueString is not None:
                rows.append(full_row(observation.valueString))
            if observation.comment is not None:
                rows.append(full_row(observation.comment))
            return

        has_value = any(k.startswith("value") and v is not None for k, v in observation.as_json().items())
        if members[resource_key]:
            groups.append(resource_key)
            rows.append(full_row(f"Test group: {label(observation)}"))
            if not has_value and observation.comment:
                rows.append(full_row(observation.comment))
            if not has_value and observation.interpretation:
                interpretation = observation.interpretation
                text = interpretation.text or "; ".join(c.display or c.code or "" for c in interpretation.coding or [])
                rows.append(full_row(f"Group interpretation: {text}"))

        # A group caption does not become an invented analyte. Unexpected
        # values on groups are retained using the existing result converter.
        if not members[resource_key] or has_value:
            converted = await results.create_result_component(observation, group_time)
            components.append({"observation": converted.entry.model_dump(by_alias=True, exclude_none=True)})
            rows.append({"td": converted.row.cells})
            result_keys.append(resource_key)
        for comment in comments[resource_key]:
            await render(comment, path + (resource_key,))
        for member in members[resource_key]:
            await render(member, path + (resource_key,))

    for resource_key in scope:
        if resource_key not in parents and not is_comment(all_obs[resource_key]):
            await render(resource_key)
    for resource_key in direct:
        if is_comment(all_obs[resource_key]) and resource_key not in attached:
            rows.append(full_row("Report-level filing"))
            await render(resource_key)
    for resource_key in scope:
        if resource_key not in emitted:
            rows.append(full_row("Unplaced source item"))
            await render(resource_key)
    assert emitted == set(scope)

    # Keep category components exactly as emitted by the existing converter.
    category = [
        c
        for c in baseline.organizer.get("component", [])
        if any(t.get("@root") == "1.2.840.114350.1.72.3.4" for t in c["observation"].get("templateId", []))
    ]
    organizer = deepcopy(baseline.organizer)
    organizer["component"] = components + category
    table = deepcopy(baseline.table)
    table["table"][0]["tbody"]["tr"] = rows
    AUDIT.append(
        {
            "report_id": report.id,
            "caption": table["caption"],
            "groups": [{"reference": k, "label": label(all_obs[k]), "members": members[k]} for k in groups],
            "results": result_keys,
            "source_observations": list(scope),
            "existing_components": len(baseline.organizer.get("component", [])),
            "proposed_components": len(organizer["component"]),
        }
    )
    return results.InvestigationWithTable(organizer=organizer, table=table)


async def convert(use_prototype):
    """Parse a fresh bundle so existing helpers cannot mutate the other run."""
    for section in ("ALLERGIES", "MEDICATION", "PROBLEMS", "IMMUNISATIONS"):
        os.environ[f"GP_CONNECT_INCLUDE_{section}"] = "false"
    os.environ["GP_CONNECT_INCLUDE_INVESTIGATIONS"] = "true"
    bundle = Bundle(json.loads(FIXTURE.read_text()))
    index = {}
    for entry in bundle.entry:
        if entry.resource and entry.resource.id:
            index[key(entry.resource)] = entry.resource
            if entry.fullUrl:
                index[entry.fullUrl] = entry.resource
    original = fhir2ccda.investigation
    try:
        if use_prototype:
            fhir2ccda.investigation = prototype
        return await fhir2ccda.convert_bundle(bundle, index)
    finally:
        fhir2ccda.investigation = original


async def main():
    """Write both documents and verify differences stay within investigations."""
    if (OUT / "existing.xml").exists():
        raise SystemExit("Comparison artifacts already exist; preserve the reviewed pre-implementation baseline.")
    existing = await convert(False)
    proposed = await convert(True)
    # Eliminate an incidental generation-second difference, if any.
    proposed["ClinicalDocument"]["effectiveTime"] = deepcopy(existing["ClinicalDocument"]["effectiveTime"])
    a, b = deepcopy(existing), deepcopy(proposed)
    for document in (a, b):
        sections = document["ClinicalDocument"]["component"]["structuredBody"]["component"]
        for item in sections:
            if item["section"].get("code", {}).get("@code") == "30954-2":
                item["section"]["text"] = None
                for entry in item["section"].get("entry", []):
                    entry["organizer"]["component"] = None
    assert a == b, "Unexpected difference outside result membership/narrative"
    assert len(AUDIT) == 20
    for name, document in [("existing.xml", existing), ("proposed-grouping.xml", proposed)]:
        xml = xmltodict.unparse(document, pretty=True)
        xml = xml.replace("&lt;br /&gt;", "<br/>").replace("&lt;br&gt;", "<br/>")
        ET.fromstring(xml)
        (OUT / name).write_text(xml)
    (OUT / "comparison.json").write_text(
        json.dumps(
            {
                "fixture": str(FIXTURE.relative_to(ROOT)),
                "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                "reports": AUDIT,
                "verification": "Both XML documents parse; 20 reports retained; only investigation narrative and organizer components differ.",
            },
            indent=2,
        )
    )
    print(f"Wrote comparison to {OUT}; reports={len(AUDIT)}; groups={sum(len(r['groups']) for r in AUDIT)}")


if __name__ == "__main__":
    asyncio.run(main())
