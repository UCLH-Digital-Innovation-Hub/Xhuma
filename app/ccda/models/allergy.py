"""C-CDA allergy concern, allergy/intolerance, and reaction models."""

from typing import List, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .base import Act, Entry, EntryRelationship, Observation
from .datatypes import CD, CE, CS, II, IVL_TS, IVXB_TS


class PlayingEntity(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    classCode: Literal["MMAT"] = Field(alias="@classCode", default="MMAT")
    code: CE


class ParticipantRole(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    templateId: Optional[List[II]] = None
    classCode: Literal["MANU"] = Field(alias="@classCode", default="MANU")
    id: Optional[List[II]] = None
    playingEntity: PlayingEntity


class Participant2(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    templateId: Optional[List[II]] = None
    typeCode: Literal["CSM"] = Field(alias="@typeCode", default="CSM")
    participantRole: ParticipantRole


class AllergyEffectiveTime(IVL_TS):
    """Clinical onset/resolution interval, independent of the assertion date."""

    low: IVXB_TS


class AssertionCode(CD):
    code: Literal["ASSERTION"] = Field(alias="@code", default="ASSERTION")
    codeSystem: Literal["2.16.840.1.113883.5.4"] = Field(alias="@codeSystem", default="2.16.840.1.113883.5.4")


class CompletedStatus(CS):
    code: Literal["completed"] = Field(alias="@code", default="completed")


class ReactionObservation(Observation):
    """Reaction Observation (2.16.840.1.113883.10.20.22.4.9)."""

    model_config = ConfigDict(populate_by_name=True)

    classCode: Literal["OBS"] = Field(alias="@classCode", default="OBS")
    moodCode: Literal["EVN"] = Field(alias="@moodCode", default="EVN")
    templateId: List[II] = Field(
        default_factory=lambda: [II(root="2.16.840.1.113883.10.20.22.4.9", extension="2014-06-09")],
        min_length=1,
    )
    id: List[II] = Field(default_factory=lambda: [II(root=str(uuid4()))], min_length=1)
    code: AssertionCode = Field(default_factory=AssertionCode)
    statusCode: CompletedStatus = Field(default_factory=CompletedStatus)
    value: CD


class AllergyReaction(EntryRelationship):
    model_config = ConfigDict(populate_by_name=True)

    typeCode: Literal["MFST"] = Field(alias="@typeCode", default="MFST")
    inversionInd: Literal[True] = Field(alias="@inversionInd", default=True)
    observation: ReactionObservation

    @field_validator("inversionInd", mode="before")
    @classmethod
    def parse_inversion(cls, value):
        return True if value in ("true", "1") else value

    @field_serializer("inversionInd")
    def serialize_inversion(self, value: bool) -> str:
        # xmltodict otherwise emits Python's "True", which is not an XML boolean.
        return "true" if value else "false"


class AllergyComment(EntryRelationship):
    """Comment activity carrying source notes alongside allergy reactions."""

    act: Act


class AllergyIntoleranceObservation(Observation):
    """Allergy - Intolerance Observation, including its inherited template ID.

    Callers supply the clinical interval and allergy/intolerance type. Terminology
    value-set membership must be checked by the caller or a CDA validator.
    """

    model_config = ConfigDict(populate_by_name=True)

    classCode: Literal["OBS"] = Field(alias="@classCode", default="OBS")
    moodCode: Literal["EVN"] = Field(alias="@moodCode", default="EVN")
    templateId: List[II] = Field(
        default_factory=lambda: [
            II(root="2.16.840.1.113883.10.20.24.3.90", extension="2014-06-09"),
            II(root="2.16.840.1.113883.10.20.22.4.7", extension="2014-06-09"),
        ],
        min_length=2,
    )
    id: List[II] = Field(default_factory=lambda: [II(root=str(uuid4()))], min_length=1)
    negationInd: Optional[bool] = Field(alias="@negationInd", default=None)
    code: AssertionCode = Field(default_factory=AssertionCode)
    statusCode: CompletedStatus = Field(default_factory=CompletedStatus)
    effectiveTime: AllergyEffectiveTime
    value: CD
    interpretationCode: Optional[List[CE]] = None
    participant: Optional[List[Participant2]] = None
    entryRelationship: Optional[List[Union[AllergyReaction, AllergyComment]]] = None

    @field_serializer("negationInd")
    def serialize_negation(self, value: Optional[bool]) -> Optional[str]:
        return None if value is None else ("true" if value else "false")


class AllergyObservation(EntryRelationship):
    model_config = ConfigDict(populate_by_name=True)

    typeCode: Literal["SUBJ"] = Field(alias="@typeCode", default="SUBJ")
    observation: AllergyIntoleranceObservation


class Allergy(Act):
    """CDA Allergy Concern Act; defaults to active for current allergies."""

    templateId: List[II] = Field(
        default_factory=lambda: [II(root="2.16.840.1.113883.10.20.22.4.30", extension="2015-08-01")]
    )
    id: List[II] = Field(default_factory=lambda: [II(root=str(uuid4()))], min_length=1)
    code: CD = Field(default_factory=lambda: CD(code="CONC", codeSystem="2.16.840.1.113883.5.6"))
    statusCode: CS = Field(default_factory=lambda: CS(code="active"))
    entryRelationship: List[AllergyObservation] = Field(min_length=1)


class AllergyEntry(Entry):
    """Typed wrapper that preserves allergy fields during Pydantic serialization."""

    act: Allergy
