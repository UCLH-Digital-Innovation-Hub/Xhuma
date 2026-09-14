import pytest
import xmltodict
from fhirclient.models import bundle
from fhirclient.models import list as fhirlist

from app.ccda.entries.results import investigation
from app.ccda.models.base import ResultObservation, ResultsOrganizer
from app.ccda.models.datatypes import CD, ED, IVL_PQ, IVXB_PQ, PQ
from app.tests.configure_tests import load_bundle

NHS_NUMBER = "9692136744"


@pytest.fixture
def investigation_reports():
    results_bundle = load_bundle(NHS_NUMBER)
    results_bundle["entry"] = [
        entry for entry in results_bundle["entry"] if "fhir_comments" not in entry
    ]
    fhir_bundle = bundle.Bundle(results_bundle)

    bundle_index = {}
    for entry in fhir_bundle.entry:
        resource = getattr(entry, "resource", None)
        if resource and getattr(resource, "id", None):
            bundle_index[f"{resource.resource_type}/{resource.id}"] = resource

    investigation_list = next(
        entry.resource
        for entry in fhir_bundle.entry
        if isinstance(entry.resource, fhirlist.List)
        and entry.resource.code
        and entry.resource.title == "Investigations and results"
    )

    reports = [
        bundle_index[list_entry.item.reference]
        for list_entry in investigation_list.entry
    ]

    return reports, bundle_index


@pytest.mark.asyncio
async def test_processes_all_investigation_reports_from_9692136744(
    investigation_reports,
):
    reports, bundle_index = investigation_reports

    processed_reports = [
        await investigation(report, bundle_index) for report in reports
    ]

    assert len(processed_reports) == 32
    for processed_report in processed_reports:
        assert processed_report.organizer["statusCode"] is not None
        assert processed_report.table["caption"]
        assert processed_report.table["table"][0]["thead"]["tr"]["th"] == [
            "Component",
            "Value",
            "Reference Range",
            "Comments",
        ]
        assert processed_report.table["table"][0]["tbody"]["tr"]

        xml = xmltodict.unparse({"xml": processed_report.table})
        assert "<caption>" in xml
        assert "<table>" in xml


@pytest.mark.asyncio
async def test_glucose_tolerance_report_keeps_category_and_comment_rows(
    investigation_reports,
):
    reports, bundle_index = investigation_reports
    glucose_report = next(
        report for report in reports if report.id == "c200000000000000_6237000000000000"
    )

    processed_report = await investigation(glucose_report, bundle_index)
    rows = processed_report.table["table"][0]["tbody"]["tr"]

    assert (
        processed_report.table["caption"]
        == "Glucose tolerance test 2023-03-30 00:00:00+01:00"
    )
    assert len(processed_report.organizer["component"]) == 1
    assert (
        processed_report.organizer["component"][0]["observation"]["value"]["@code"]
        == "16"
    )
    assert processed_report.organizer["component"][0]["observation"]["value"][
        "@codeSystem"
    ] == ("1.2.840.114350.1.72.1.5007")
    assert rows == [
        {
            "td": [
                {
                    "@colspan": 4,
                    "#text": (
                        "Original text: Glucose tolerance test\n\n"
                        "Abnormality indicator: Abnormal\r\n"
                        "Clinical Information: DIABETIC\n\n"
                        "this report has a results indicator changed to abnormal "
                        "and a follow up action of repeat test"
                    ),
                }
            ]
        },
        {
            "td": [
                {
                    "@colspan": 4,
                    "#text": (
                        "Title: Glucose tolerance test\n"
                        "Result indicator: Unknown\n"
                        "Message: Report ID: 1013/CH2101128T/202303301621\n"
                        "Specimen ID: CH2101128T\n"
                        "Specimen description: BLOOD & URINE\n"
                        "Patient Informed Details: Patient does not need to be informed\n"
                        "Follow Up Action: Other"
                    ),
                }
            ]
        },
    ]


@pytest.mark.asyncio
async def test_fbc_report_flags_out_of_range_values(investigation_reports):
    reports, bundle_index = investigation_reports
    fbc_report = next(
        report for report in reports if report.id == "c200000000000000_6437000000000000"
    )

    processed_report = await investigation(fbc_report, bundle_index)
    rows = processed_report.table["table"][0]["tbody"]["tr"]
    platelet_row = next(row for row in rows if row["td"][0] == "Platelet count")

    assert processed_report.table["caption"] == (
        "FBC - full blood count 2024-01-20 10:46:00+00:00"
    )
    assert len(processed_report.organizer["component"]) == 13
    assert platelet_row["td"][1] == {
        "content": {"@styleCode": "flagData", "#text": "497 10^9/L"}
    }
    assert platelet_row["td"][2] == {"#text": "150 - 450 10^9/L"}
    assert (
        "Above high reference limit"
        in platelet_row["td"][4]["content"]["content"][0]["#text"]
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            CD(code="260385009", codeSystem="2.16.840.1.113883.6.96"), id="coded"
        ),
        pytest.param(PQ(value=5.2, unit="mmol/L"), id="quantity"),
        pytest.param(IVL_PQ(high=IVXB_PQ(value=5, unit="mg/L")), id="interval"),
        pytest.param(ED(xmlText="Laboratory narrative"), id="encapsulated-data"),
        pytest.param({"@xsi:type": "ST", "#text": "Not detected"}, id="string"),
        pytest.param({"@xsi:type": "BL", "@value": "true"}, id="boolean"),
        pytest.param({"@xsi:type": "INT", "@value": 3}, id="integer"),
        pytest.param({"@xsi:type": "REAL", "@value": 1.25}, id="real"),
        pytest.param(
            {
                "@xsi:type": "CD",
                "@code": "260385009",
                "@codeSystem": "2.16.840.1.113883.6.96",
            },
            id="coded-dictionary",
        ),
        pytest.param(
            {
                "@xsi:type": "IVL_PQ",
                "high": {"@value": 5, "@unit": "mg/L", "@inclusive": "false"},
            },
            id="interval-dictionary",
        ),
    ],
)
def test_result_value_preserves_cda_datatype_through_serialization(value):
    expected = (
        value.model_dump(by_alias=True, exclude_none=True)
        if hasattr(value, "model_dump")
        else value
    )
    observation = ResultObservation(value=value)
    organizer = ResultsOrganizer(component=[{"observation": observation}])
    serialized = organizer.model_dump(by_alias=True, exclude_none=True)

    assert serialized["component"][0]["observation"]["value"] == expected
    # Revalidate the dictionary representation without losing datatype-specific fields.
    restored = ResultsOrganizer.model_validate(serialized)
    assert restored.model_dump(by_alias=True, exclude_none=True) == serialized
    xml = xmltodict.unparse({"organizer": serialized})
    assert f'xsi:type="{expected["@xsi:type"]}"' in xml
