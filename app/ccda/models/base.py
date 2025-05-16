from typing import Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, field_serializer

from .datatypes import CD, CE, CS, ED, EIVL_TS, II, IVL_PQ, IVL_TS, PIVL_TS, SXCM_TS


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


class Observation(BaseModel):
    """
    Representation of CDA model object Observation. Only contain relevant attributes.
    """

    classCode: str = Field(alias="@classCode", default="OBS")
    moodCode: str = Field(alias="@moodCode", default="EVN")
    templateId: List[II] = Field(default_factory=list)
    id: Optional[List[II]] = Field(default_factory=list)
    code: Optional[CD] = None
    text: Optional[str] = None
    statusCode: Optional[CS] = None
    effectiveTime: Optional[IVL_TS] = None
    value: Optional[dict] = None
    entryRelationship: List["EntryRelationship"] = Field(default_factory=list)


class InstructionObservation(Observation):
    """
    Representation of CDA model object Instruction Observation.
    """

    templateId: List[II] = Field(
        default=[
            II(
                **{
                    "@root": "2.16.840.1.113883.10.20.22.4.515",
                    "@extension": "2025-05-01",
                }
            )
        ]
    )
    code: Optional[CD] = Field(
        default=CD(
            **{
                "@code": "89187-7",
                "@codeSystem": "2.16.840.1.113883.6.1",
            }
        )
    )
    statusCode: Optional[CS] = CS(
        **{
            "@code": "completed",
        }
    )
    # value: Optional[CD]  = {
    #     "@code": "422037009",
    #     "@codeSystem": "2.16.840.1.113883.6.96",
    #     "@displayName": "Provider medication administration instructions",
    #     "@codeSystemName": "SNOMED CT",
    # }


class EntryRelationship(BaseModel):
    # act: EntryRelationshipAct
    typeCode: str = Field(alias="@typeCode", default="SUBJ")
    inversionInd: Optional[bool] = Field(alias="@inversionInd", default=None)
    sequenceNumber: Optional[int] = None
    act: Optional[Act] = None
    observation: Optional[Observation] = None


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
    effectiveTime: List[Union[SXCM_TS, IVL_TS, PIVL_TS, EIVL_TS]] = Field(
        default_factory=list
    )
    consumable: Optional[Consumable] = None
    routeCode: Optional[CE] = None
    doseQuantity: Optional[IVL_PQ] = None
    rateQuantity: Optional[IVL_PQ] = None
    entryRelationship: List[EntryRelationship] = Field(default_factory=list)
    # precondition: List[Precondition] = Field(default_factory=list)

    @field_serializer("effectiveTime")
    def serialize_effective_time(
        self, sxcm_ts_list: List[Union[SXCM_TS, IVL_TS, PIVL_TS, EIVL_TS]]
    ) -> Dict:
        """
        Takes a list of SXCM_TS objects and returns a dictionary with operator as key
        """
        # print(sxcm_ts_list)
        time_list = []
        for eff_time in sxcm_ts_list:
            # print(f"eff_time: {eff_time}")
            # print(isinstance(eff_time, SXCM_TS))
            if eff_time.resource_type == "SXCM_TS":
                time_list.append(
                    {
                        eff_time.operator: (
                            {"@value": eff_time.value}
                            if eff_time.value is not None
                            else None
                        )
                    }
                )
            else:
                time_list.append(eff_time.model_dump(by_alias=True, exclude_none=True))

        return time_list
        # print(time_list)


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


class ResultsOrganizer(BaseModel):
    """
    Representation of a CDA Results Organizer model object.
    """

    templateId: List[II] = Field(
        default=[
            II(
                **{
                    "@root": "2.16.840.1.113883.10.20.22.4.1",
                    "@extension": "2015-08-01",
                }
            )
        ],
    )
    id: Optional[List[II]] = Field(default_factory=list)
    code: Optional[CD] = None
    statusCode: Optional[CS] = None
    effectiveTime: Optional[IVL_TS] = None
    component: List[Observation] = Field(default_factory=list)


class ResultsSection(Section):
    """
    Representation of a CDA Results Section model object.
    """

    templateId: List[II] = Field(
        default=[
            II(
                **{
                    "@root": "2.16.840.1.113883.10.20.22.2.3.1",
                    "@extension": "2015-08-01",
                }
            )
        ]
    )
    code:CE = Field(default=CE(
        **{
            "@code": "30954-2",
            "@codeSystem": "2.16.840.1.113883.6.1",
        })
    )
    title: Optional[str] = "Results"
    text: Optional[str] = None
    entry: Optional[List[ResultsOrganizer]] = Field(default_factory=list)
