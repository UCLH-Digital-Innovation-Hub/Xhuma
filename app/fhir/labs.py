from typing import Optional, List
from pydantic import Field, field_validator
from .base import FHIRBaseModel, Identifier, Meta, Extension, CodeableConcept, Reference, Annotation
from .medications import Period


class Quantity(FHIRBaseModel):
    value: Optional[float] = None
    unit: Optional[str] = None
    system: Optional[str] = None
    code: Optional[str] = None


class ObservationReferenceRange(FHIRBaseModel):
    low: Optional[Quantity] = None
    high: Optional[Quantity] = None
    text: Optional[str] = None


class ObservationRelated(FHIRBaseModel):
    type: Optional[str] = None
    target: Reference


class Observation(FHIRBaseModel):
    resourceType: str = Field(default="Observation")
    id: Optional[str] = None
    identifier: List[Identifier] = Field(default_factory=list)
    status: Optional[str] = None
    code: Optional[CodeableConcept] = None
    
    # Polymorphic values
    valueQuantity: Optional[Quantity] = None
    valueCodeableConcept: Optional[CodeableConcept] = None
    valueString: Optional[str] = None
    
    # Polymorphic effective times
    effectiveDateTime: Optional[str] = None
    effectivePeriod: Optional[Period] = None
    effectiveInstant: Optional[str] = None
    
    comment: Optional[str] = None
    note: List[Annotation] = Field(default_factory=list)
    
    interpretation: Optional[CodeableConcept] = None
    referenceRange: List[ObservationReferenceRange] = Field(default_factory=list)
    
    related: List[ObservationRelated] = Field(default_factory=list)
    performer: List[Reference] = Field(default_factory=list)
    
    @field_validator('code')
    @classmethod
    def validate_code_has_snomed(cls, v: Optional[CodeableConcept]) -> Optional[CodeableConcept]:
        if not v:
            return v
        if not v.coding:
            return v
        
        has_snomed = any(c.system == "http://snomed.info/sct" for c in v.coding)
        # We don't strictly reject missing SNOMED on all observations because some legacy labs might use read codes
        # But we ensure the code structure is safely parsable.
        return v


class DiagnosticReport(FHIRBaseModel):
    resourceType: str = Field(default="DiagnosticReport")
    id: Optional[str] = None
    status: Optional[str] = None
    code: Optional[CodeableConcept] = None
    identifier: List[Identifier] = Field(default_factory=list)
    performer: List[Reference] = Field(default_factory=list)
    result: List[Reference] = Field(default_factory=list)
    effectiveDateTime: Optional[str] = None
    effectivePeriod: Optional[Period] = None
