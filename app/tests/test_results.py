import json
import pprint

from fhirclient.models import bundle
from fhirclient.models import list as fhirlist

from app.ccda.entries import result as result_entry

with open("app/tests/fixtures/bundles/pathologyexample.json") as f:
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
lists = [l for l in lists if l.code and l.title == "Investigations and Results"]

for l in lists:
    print(f"List: {l.title}")
    for entry in l.entry:
        print(entry.item.reference)
        resource = bundle_index.get(entry.item.reference)
        if resource:
            print(f"  - {resource.resource_type}: {resource.id}")
            print(
                f"    - {resource.code.coding[0].display if hasattr(resource, 'code') else 'No code'}"
            )
            if resource.result:
                for result in resource.result:
                    print(f"      - {result.reference}")
                    result_resource = bundle_index.get(result.reference)
                    if result_resource:
                        print(
                            f"          - {result_resource.code.coding[0].display if hasattr(result_resource, 'code') else 'No code'}"
                        )
                        # check if result_resource related has type of "has-member"
                        # if resource related is not none:
                        if (
                            hasattr(result_resource, "related")
                            and result_resource.related
                        ):
                            print(
                                f"            - Related Resources:{len(result_resource.related)}"
                            )
                            for related in result_resource.related:
                                if related.type == "has-member":
                                    related_resource = bundle_index.get(
                                        related.target.reference
                                    )
                                    if related_resource:
                                        print(
                                            f"             - {related_resource.code.coding[0].display if hasattr(related_resource, 'code') else 'No code'}"
                                        )
                                    else:
                                        print("            - No related resource found")
                    else:
                        print("        - No resource found")

        else:
            print("  - No resource found")

for l in lists:
    for entry in l.entry:
        resource = bundle_index.get(entry.item.reference)
        for r in resource.result:
            result_resource = bundle_index.get(r.reference)
            # pprint.pprint(result_resource.as_json())
            pprint.pprint(result_entry(result_resource, bundle_index))
