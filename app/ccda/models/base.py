from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .datatypes import CD, CE, CS, ED, II, IVL_PQ, IVL_TS, SXCM_TS


class ManufacturedMaterial(BaseModel):
    code: CD


class ManufacturedProduct(BaseModel):
    manufacturedMaterial: ManufacturedMaterial
    templateId: List[II] = Field(default_factory=list)
    id: II = {"@root": str(uuid4())}
    classCode: str = Field(default="MANU", alias="@classCode")


class Consumable(BaseModel):
    manufacturedProduct: ManufacturedProduct


class EntryRelationshipAct(BaseModel):
    templateId: II
    code: CD
    text: Dict
    statusCode: Optional[CS] = None
    classCode: str = Field(alias="@classCode", default="ACT")
    moodCode: str = Field(alias="@moodCode", default="INT")


class Act(BaseModel):
    """
    Representation of CDA model object Act. Only contain relevant attributes.
    """

    classCode: str = Field(alias="@classCode", default="ACT")
    moodCode: str = Field(alias="@moodCode", default="EVN")
    templateId: List[II] = Field(default_factory=list)
    id: Optional[List[II]] = Field(default_factory=list)
    code: Optional[CD] = None
    text: Optional[str] = None
    statusCode: Optional[CS] = None
    effectiveTime: Optional[IVL_TS] = None


class EntryRelationship(BaseModel):
    # act: EntryRelationshipAct
    typeCode: str = Field(alias="@typeCode", default="SUBJ")
    inversionInd: Optional[bool] = Field(alias="@inversionInd", default=None)
    sequenceNumber: Optional[int] = None
    act: Optional[Act] = None


class SubstanceAdministration(BaseModel):
    """
    Representation of CDA model object Substance Administration. Only contain relevant attributes.
    https://gazelle.ihe.net/CDAGenerator/cda/POCDMT000040SubstanceAdministration.html
    """

    classCode: str = Field(alias="@classCode", default="SBADM")
    moodCode: str = Field(alias="@moodCode", default="INT")
    templateId: List[II] = Field(default_factory=list)
    id: List[II] = Field(default_factory=list)
    code: Optional[CD] = Field(
        default=CD(
            **{
                "@code": "CONC",
                "@codeSystem": "2.16.840.1.113883.5.6",
            }
        )
    )
    text: Optional[ED] = None
    statusCode: Optional[CS] = None
    effectiveTime: List[SXCM_TS] = Field(default_factory=list)
    consumable: Optional[Consumable] = None
    routeCode: Optional[CE] = None
    doseQuantity: Optional[IVL_PQ] = None
    entryRelationship: List[EntryRelationship] = Field(default_factory=list)
    # precondition: List[Precondition] = Field(default_factory=list)


class Entry(BaseModel):
    """
    Representation of a CDA Entry model object; ignoring all attributes and feature that are
    not relevant to what we get from Epic NoteReader messages - we only need Act.
    """

    act: Optional[Act] = None
    substanceAdministration: Optional[SubstanceAdministration] = None


class Section(BaseModel):
    """
    Representation of a generic section in a CDA document. To add more attributes if needed
    """

    id: Optional[II] = None
    templateId: List[II] = Field(default_factory=list)
    code: Optional[CE] = None
    title: Optional[str] = None
    text: Optional[str] = None
    entry: List[Entry] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
