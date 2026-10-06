"""CDA specimen participation for a results organizer."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .admin import PlayingEntity
from .datatypes import II


class SpecimenPlayingEntity(PlayingEntity):
    """The material tested; collection/receipt dates are not entity properties."""

    determinerCode: Literal["INSTANCE"] = Field(default="INSTANCE", alias="@determinerCode")


class SpecimenRole(BaseModel):
    """Identify the sample and its material within the report."""

    model_config = ConfigDict(populate_by_name=True)
    classCode: Literal["SPEC"] = Field(default="SPEC", alias="@classCode")
    id: list[II] | None = None
    specimenPlayingEntity: SpecimenPlayingEntity


class Specimen(BaseModel):
    """Specimen participation; Epic collection time belongs to the organizer.

    Receipt time is separate laboratory metadata, not the organizer collection
    time or component finalising time. Neither is invented on this participant.
    """

    model_config = ConfigDict(populate_by_name=True)
    typeCode: Literal["SPC"] = Field(default="SPC", alias="@typeCode")
    specimenRole: SpecimenRole
