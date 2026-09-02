from typing import List

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.ccda.helpers import templateId
from app.ccda.models.datatypes import II, CD, IVL_PQ, IVXB_PQ


def test_ii_valid_data():
    data = {
        "@extension": "12345",
        "@root": "1.2.3.4.5",
        "assigningAuthorityName": "Test Authority",
        "displayable": True,
    }
    ii_instance = II(**data)
    assert ii_instance.extension == "12345"
    assert ii_instance.root == "1.2.3.4.5"
    assert ii_instance.assigningAuthorityName == "Test Authority"
    assert ii_instance.displayable is True


def test_ii_missing_optional_fields():
    data = {
        "@root": "1.2.3.4.5",
    }
    ii_instance = II(**data)
    assert ii_instance.root == "1.2.3.4.5"
    assert ii_instance.extension is None
    assert ii_instance.assigningAuthorityName is None
    assert ii_instance.displayable is None


def test_ii_invalid_data():
    data = {
        "@extension": 12345,  # Invalid type, should be a string
        "@root": "1.2.3.4.5",
    }
    with pytest.raises(ValidationError):
        II(**data)


def test_ii_alias_population():
    data = {
        "extension": "12345",
        "root": "1.2.3.4.5",
    }
    ii_instance = II(**data)
    assert ii_instance.extension == "12345"
    assert ii_instance.root == "1.2.3.4.5"


class MockII(BaseModel):
    template_Id: List[II] = Field(default_factory=list)


def test_templateId():
    root = "2.16.840.1.113883."
    extension = "2014-06-09"
    template_id = templateId(root, extension)
    assert len(template_id) == 2
    assert template_id[0]["@root"] == root
    assert template_id[1]["@root"] == root
    assert template_id[1]["@extension"] == extension
    # assert template_id[0]["@extension"] is None
    # assert extension doesn't exist in the first element
    assert template_id[0].get("@extension") is None


def test_templateID_inclass():
    root = "2.16.840.1.113883."
    extension = "2014-06-09"
    # test_instance = MockII(templateId=templateId(root, extension))
    test_instance = MockII(**{"template_Id": templateId(root, extension)})

    assert len(test_instance.template_Id) == 2
    assert test_instance.template_Id[0].root == root
    assert test_instance.template_Id[1].root == root
    assert test_instance.template_Id[1].extension == extension
    assert test_instance.template_Id[0].extension is None

    dumped = test_instance.model_dump(by_alias=True, exclude_none=True)
    assert len(dumped["template_Id"]) == 2
    assert dumped["template_Id"][0]["@root"] == root
    assert dumped["template_Id"][1]["@root"] == root
    assert dumped["template_Id"][1]["@extension"] == extension
    assert dumped["template_Id"][0].get("@extension") is None


def test_resource_type_absence_in_serialization():
    cd = CD(**{"@code": "123", "@codeSystemName": "LOINC"})
    dumped = cd.model_dump(by_alias=True, exclude_none=True)
    assert "resource_type" not in dumped
    assert dumped["@xsi:type"] == "CD"
    assert dumped["@code"] == "123"


def test_ivl_pq_serialization():
    ivl = IVL_PQ(
        low=IVXB_PQ(**{"@value": 1.0, "@unit": "mg"}),
        high=IVXB_PQ(**{"@value": 2.0, "@unit": "mg"}),
    )
    dumped = ivl.model_dump(by_alias=True, exclude_none=True)

    assert "resource_type" not in dumped
    assert dumped["@xsi:type"] == "IVL_PQ"

    assert "resource_type" not in dumped["low"]
    assert dumped["low"]["@xsi:type"] == "IVXB_PQ"
    assert dumped["low"]["@value"] == 1.0


def test_no_xsi_type_inheritance():
    from app.ccda.models.datatypes import II, TEL, CS, AD
    
    ii = II(**{"@root": "1.2.3"})
    assert "@xsi:type" not in ii.model_dump(by_alias=True, exclude_none=True)
    
    tel = TEL(**{"@value": "tel:123"})
    assert "@xsi:type" not in tel.model_dump(by_alias=True, exclude_none=True)
    
    cs = CS(**{"@code": "xyz"})
    assert "@xsi:type" not in cs.model_dump(by_alias=True, exclude_none=True)
    
    ad = AD(**{"city": "London"})
    assert "@xsi:type" not in ad.model_dump(by_alias=True, exclude_none=True)

def test_recursive_no_any_xsi_type():
    from app.ccda.models.base import Observation
    from app.ccda.models.datatypes import ANY, CD, ST, PQ
    
    obs = Observation(
        code=CD(code="123", codeSystem="1.2.3"),
        value=ST(text="Some text")
    )
    dumped = obs.model_dump(by_alias=True, exclude_none=True)
    
    def walk(obj):
        if isinstance(obj, dict):
            assert obj.get("@xsi:type") != "ANY", "Accidental ANY xsi:type leaked"
            assert "resource_type" not in obj, "Accidental resource_type leaked"
            for k, v in obj.items():
                walk(v)
        elif isinstance(obj, list):
            for i in obj:
                walk(i)
                
    walk(dumped)

