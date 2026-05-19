import asyncio
import json
import pprint

import xmltodict
from fhirclient.models import bundle
from fhirclient.models import list as fhirlist

from app.ccda.entries import result as result_entry
from app.ccda.results import investigation

with open("app/tests/fixtures/bundles/9692136744.json") as f:
    # with open("app/tests/fixtures/bundles/9690937286.json") as f:
    results_bundle = json.load(f)

comment_index = None
for j, i in enumerate(results_bundle["entry"]):
    if "fhir_comments" in i.keys():
        comment_index = j
if comment_index is not None:
    results_bundle["entry"].pop(comment_index)
fhir_bundle = bundle.Bundle(results_bundle)

bundle_index = {}
for entry in fhir_bundle.entry:
    try:
        address = f"{entry.resource.resource_type}/{entry.resource.id}"
        bundle_index[address] = entry.resource
    except:
        pass

lists = [
    entry.resource
    for entry in fhir_bundle.entry
    if isinstance(entry.resource, fhirlist.List)
]
for l in lists:
    print(f"List ID: {l.id}, Title: {l.title}, Status: {l.status}")

# only have investigations for now
lists = [l for l in lists if l.code and l.title == "Investigations and results"]


for l in lists:
    # print(f"List: {l.title}")
    for entry in l.entry:
        resource = bundle_index.get(entry.item.reference)
        # print(resource)

        #     observations: list[obs.Observation] = (
        #     [index[x] for x in diagnostic_report.result] if diagnostic_report.result else []
        # )
        # for result in resource.result:
        # print(bundle_index.get(result.reference))
        # pprint.pprint(result_entry(resource, bundle_index))
        organizerwithtable = asyncio.run(investigation(resource, bundle_index))
        xml_dict = {"xml": organizerwithtable.table}
        xml_table = xmltodict.unparse(xml_dict, pretty=True)
        print(xml_table)

        # pprint.pprint(organizerwithtable.organizer)

        # print table to console
        # for row in organizerwithtable.table.rows:
        #     print(row)
