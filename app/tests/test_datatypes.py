from typing import Dict, List, Optional

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.ccda.helpers import templateId
from app.ccda.models.datatypes import II


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


class TestII(BaseModel):
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
    # test_instance = TestII(templateId=templateId(root, extension))
    test_instance = TestII(**{"template_Id": templateId(root, extension)})

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
