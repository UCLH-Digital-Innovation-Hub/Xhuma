from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict


class FHIRBaseModel(BaseModel):
    """
    Base Pydantic model for inbound FHIR parsing.
    Ignores extra fields that we don't need to parse, allowing for graceful 
    handling of massively bloated GP Connect payloads.
    """
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore"
    )


class Extension(FHIRBaseModel):
    url: str
    valueString: Optional[str] = None
    # Use string to avoid circular dependency, though pydantic handles it with model_rebuild
    valueReference: Optional["Reference"] = None
    valueCodeableConcept: Optional["CodeableConcept"] = None


class Coding(FHIRBaseModel):
    system: Optional[str] = None
    code: Optional[str] = None
    display: Optional[str] = None


class CodeableConcept(FHIRBaseModel):
    coding: List[Coding] = Field(default_factory=list)
    text: Optional[str] = None


class Reference(FHIRBaseModel):
    reference: Optional[str] = None
    display: Optional[str] = None


class Identifier(FHIRBaseModel):
    system: Optional[str] = None
    value: str


class Meta(FHIRBaseModel):
    profile: List[str] = Field(default_factory=list)


class Annotation(FHIRBaseModel):
    authorReference: Optional[Reference] = None
    authorString: Optional[str] = None
    time: Optional[datetime] = None
    text: str


Extension.model_rebuild()
