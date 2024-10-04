import asyncio
import pprint

from app.pds import pds

test_nhs_numbers = [
    9690937278,
    9690937286,
    9690937294,
    9690937308,
    9690937375,
    9690937383,
    9690937391,
    9690937405,
    9690938533,
    9690938541,
    9690938622,
    9690938614,
    9690938096,
    9690938576,
    9690938118,
    9690938126,
    9690938681,
    9690938134,
]


async def test_pds(nhsno):
    pds_search = await pds.lookup_patient(nhsno)
    fhir_endpoint = None
    # print(sds_search["entry"][0]["resource"]["address"])

    try:
        gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]
        sds_search = await pds.sds_trace(
            pds_search["generalPractitioner"][0]["identifier"]["value"],
            endpoint=True,
        )
        try:
            fhir_endpoint = sds_search["entry"][0]["resource"]["address"]
        except:
            pass
            # print(sds_search)
    except:
        if pds_search["meta"]["security"]:
            gp_ods = "restricted"
        else:
            gp_ods = None
            pprint.pprint(pds_search)

    # asid = sds_search["entry"][0]["resource"]["identifier"][0]["value"]
    print(f"Patient: {nhsno}, GPODS:{gp_ods}, endpoint:{fhir_endpoint}")


if __name__ == "__main__":
    for nhsno in test_nhs_numbers:
        # try:
        #
        # except:
        #     print(f"Patient {nhsno} not complete")
        asyncio.run(test_pds(nhsno))
    base_sds = asyncio.run(pds.sds_trace("B83621"))
    pprint.pprint(base_sds)

    base_sds = asyncio.run(pds.sds_trace("B83621", endpoint=True))
    pprint.pprint(base_sds)
