from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Extra, Field, field_serializer

from .admin import AuthorParticipation
from .datatypes import (
    ANY,
    CD,
    CE,
    CS,
    ED,
    EIVL_TS,
    II,
    IVL_INT,
    IVL_PQ,
    IVL_TS,
    PIVL_TS,
    PQ,
    RTO_PQ_PQ,
    SXCM_TS,
)


class ManufacturedMaterial(BaseModel):
    code: CD
    lotNumberText: str | None = None


class ManufacturedProduct(BaseModel):
    manufacturedMaterial: ManufacturedMaterial
    templateId: list[II] = Field(default_factory=list)
    id: II = {"@root": str(uuid4())}
    classCode: str = Field(default="MANU", alias="@classCode")


class Consumable(BaseModel):
    manufacturedProduct: ManufacturedProduct


class EntryRelationshipAct(BaseModel):
    templateId: II
    code: CD
    text: str | None = None
    statusCode: CS | None = None
    classCode: str = Field(alias="@classCode", default="ACT")
    moodCode: str = Field(alias="@moodCode", default="INT")


class Act(BaseModel):
    """
    Representation of CDA model object Act. Only contain relevant attributes.
    """

    classCode: str = Field(alias="@classCode", default="ACT")
    moodCode: str = Field(alias="@moodCode", default="EVN")
    templateId: list[II] = Field(default_factory=list)
    id: list[II] | None = Field(default_factory=list)
    code: CD | None = None
    text: ED | None = None
    statusCode: CS | None = None
    effectiveTime: IVL_TS | None = None


class Observation(BaseModel):
    """
    Representation of CDA model object Observation. Only contain relevant attributes.
    """

    classCode: str = Field(alias="@classCode", default="OBS")
    moodCode: str = Field(alias="@moodCode", default="EVN")
    templateId: list[II] = Field(default_factory=list)
    id: list[II] | None = Field(default_factory=list)
    code: CD | None = None
    text: str | None = None
    statusCode: CS | None = None
    effectiveTime: IVL_TS | None = None
    value: ANY | None = None
    entryRelationship: list[EntryRelationship] | None = Field(default=None)


class ObservationRange(BaseModel):
    classCode: str = Field(alias="@classCode", default="OBS")
    moodCode: str = Field(alias="@moodCode", default="EVN.CRT")
    text: str | None = None
    value: ANY | None = None


class ReferenceRange(BaseModel):
    typeCode: str = Field(alias="@typeCode", default="REFV")
    observationRange: ObservationRange


class ResultObservation(Observation):
    """
    Representation of CDA model object Result Observation.
    """

    templateId: list[II] = Field(
        default=[
            II(
                **{
                    "@root": "2.16.840.1.113883.10.20.22.4.2",
                    "@extension": "2015-08-01",
                }
            )
        ]
    )
    referenceRange: list[ReferenceRange] | None = None
    value: PQ | None = None  # PQ is used for numeric values


class InstructionObservation(Observation):
    """
    Representation of CDA model object Instruction Observation.
    """

    templateId: list[II] = Field(
        default=[
            II(
                **{
                    "@root": "2.16.840.1.113883.10.20.22.4.515",
                    "@extension": "2025-05-01",
                }
            )
        ]
    )
    code: CD | None = Field(
        default=CD(
            **{
                "@code": "89187-7",
                "@codeSystem": "2.16.840.1.113883.6.1",
            }
        )
    )
    statusCode: CS | None = CS(
        **{
            "@code": "completed",
        }
    )


class Criterion(BaseModel):
    classCode: str = Field(alias="@classCode", default="OBS")
    moodCode: str = Field(alias="@moodCode", default="EVN")
    code: CD | None = None
    value: ANY | None = None


class Precondition(BaseModel):
    typeCode: str = Field(alias="@typeCode", default="PRCN")
    criterion: Criterion


class SubstanceAdministration(BaseModel):
    """
    Representation of CDA model object Substance Administration. Only contain relevant attributes.
    https://gazelle.ihe.net/CDAGenerator/cda/POCDMT000040SubstanceAdministration.html
    """

    classCode: str = Field(alias="@classCode", default="SBADM")
    moodCode: str = Field(alias="@moodCode", default="INT")
    templateId: list[II] = Field(default_factory=list)
    id: list[II] = Field(default_factory=list)
    # ?code needed
    # code: Optional[CD] = Field(
    #     default=CD(
    #         **{
    #             "@code": "CONC",
    #             "@codeSystem": "2.16.840.1.113883.5.6",
    #         }
    #     )
    # )
    code: CD | None = None
    text: str | ED | None = None
    statusCode: CS | None = None
    effectiveTime: list[SXCM_TS | IVL_TS | PIVL_TS | EIVL_TS] = Field(default_factory=list)
    consumable: Consumable | None = None
    routeCode: CE | None = None
    doseQuantity: IVL_PQ | PQ | None = None
    rateQuantity: IVL_PQ | PQ | None = None
    maxDoseQuantity: RTO_PQ_PQ | None = None
    entryRelationship: list[EntryRelationship] = Field(default_factory=list)
    repeatNumber: IVL_INT | None = None
    # TODO flesh out precondition model
    precondition: list[Precondition] | None = None

    @field_serializer("effectiveTime")
    def serialize_effective_time(self, sxcm_ts_list: list[SXCM_TS | IVL_TS | PIVL_TS | EIVL_TS]) -> list:
        """
        Takes a list of SXCM_TS objects and returns a dictionary with operator as key
        """
        # print(sxcm_ts_list)
        time_list = []
        sxcm = {}
        for eff_time in sxcm_ts_list:
            # print(f"eff_time: {eff_time}")
            # print(isinstance(eff_time, SXCM_TS))
            if eff_time.resource_type == "SXCM_TS" and getattr(eff_time, "operator", None):
                # add the operator to the dictionary
                sxcm[eff_time.operator] = {"@value": eff_time.value}
            else:
                time_list.append(eff_time.model_dump(by_alias=True, exclude_none=True))
        # append the sxcm dictionary to the time_list at the start
        if sxcm:
            time_list.insert(0, sxcm)
        return time_list
        # print(time_list)


class EntryRelationship(BaseModel, extra=Extra.allow):
    # act: EntryRelationshipAct
    typeCode: str = Field(alias="@typeCode", default="SUBJ")
    inversionInd: bool | None = Field(alias="@inversionInd", default=None)
    sequenceNumber: int | None = None
    act: Act | None = None
    observation: Observation | None = None
    substanceAdministration: SubstanceAdministration | None = None
    # accept any type of object


class Entry(BaseModel):
    """
    Representation of a CDA Entry model object; ignoring all attributes and feature that are
    not relevant to what we get from Epic NoteReader messages - we only need Act.
    """

    act: Act | None = None
    substanceAdministration: SubstanceAdministration | None = None


class Section(BaseModel):
    """
    Representation of a generic section in a CDA document. To add more attributes if needed
    """

    id: II | None = None
    templateId: list[II] = Field(default_factory=list)
    code: CE | None = None
    title: str | None = None
    text: str | None = None
    entry: list[Entry] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


class ResultsOrganizer(BaseModel):
    """
    Representation of a CDA Results Organizer model object.
    """

    classCode: str = Field(alias="@classCode", default="BATTERY")
    moodCode: str = Field(alias="@moodCode", default="EVN")
    templateId: list[II] = Field(
        default=[
            II(
                **{
                    "@root": "2.16.840.1.113883.10.20.22.4.1",
                    "@extension": "2015-08-01",
                }
            )
        ],
    )
    id: list[II] | None = Field(default_factory=list)
    code: CD | None = None
    statusCode: CS | None = None
    effectiveTime: IVL_TS | None = None
    author: AuthorParticipation | None = None
    component: list[ResultObservation] = Field(default_factory=list)


class ResultsSection(Section):
    """
    Representation of a CDA Results Section model object.
    """

    templateId: list[II] = Field(
        default=[
            II(
                **{
                    "@root": "2.16.840.1.113883.10.20.22.2.3.1",
                    "@extension": "2015-08-01",
                }
            )
        ]
    )
    code: CE = Field(
        default=CE(
            **{
                "@code": "30954-2",
                "@codeSystem": "2.16.840.1.113883.6.1",
            }
        )
    )
    title: str | None = "Results"
    text: str | None = None
    entry: list[ResultsOrganizer] | None = Field(default_factory=list)


Observation.model_rebuild()
SubstanceAdministration.model_rebuild()
EntryRelationship.model_rebuild()
