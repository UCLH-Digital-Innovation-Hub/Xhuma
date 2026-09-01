from typing import Optional, List
from pydantic import Field, field_validator
from .base import FHIRBaseModel, Identifier, Meta, Extension, CodeableConcept, Reference, Annotation


class TimingRepeat(FHIRBaseModel):
    frequency: Optional[int] = None
    frequencyMax: Optional[int] = None
    period: Optional[float] = None
    periodMax: Optional[float] = None
    periodUnit: Optional[str] = None
    when: List[str] = Field(default_factory=list)
    offset: Optional[int] = None


class Timing(FHIRBaseModel):
    repeat: Optional[TimingRepeat] = None


class DoseQuantity(FHIRBaseModel):
    value: Optional[float] = None
    unit: Optional[str] = None
    system: Optional[str] = None
    code: Optional[str] = None


class Ratio(FHIRBaseModel):
    numerator: Optional[DoseQuantity] = None
    denominator: Optional[DoseQuantity] = None


class Dosage(FHIRBaseModel):
    timing: Optional[Timing] = None
    asNeededBoolean: Optional[bool] = None
    asNeededCodeableConcept: Optional[CodeableConcept] = None
    doseQuantity: Optional[DoseQuantity] = None
    maxDosePerPeriod: Optional[Ratio] = None
    method: Optional[CodeableConcept] = None
    patientInstruction: Optional[str] = None
    text: Optional[str] = None


class Medication(FHIRBaseModel):
    resourceType: str = Field(default="Medication")
    id: Optional[str] = None
    code: CodeableConcept
    
    @field_validator('code')
    @classmethod
    def validate_code_has_snomed(cls, v: CodeableConcept) -> CodeableConcept:
        if not v.coding:
            raise ValueError("Medication.code must contain at least one Coding element")
        
        has_snomed = any(c.system == "http://snomed.info/sct" for c in v.coding)
        if not has_snomed:
            raise ValueError("Medication.code must contain a valid SNOMED CT coding (http://snomed.info/sct). Transfer-degraded data must use a fallback SNOMED code.")
            
        return v


class MedicationRequest(FHIRBaseModel):
    resourceType: str = Field(default="MedicationRequest")
    id: Optional[str] = None
    note: List[Annotation] = Field(default_factory=list)
    dosageInstruction: List[Dosage] = Field(default_factory=list)


class Period(FHIRBaseModel):
    start: Optional[str] = None
    end: Optional[str] = None


class MedicationStatement(FHIRBaseModel):
    resourceType: str = Field(default="MedicationStatement")
    id: Optional[str] = None
    identifier: List[Identifier] = Field(default_factory=list)
    status: Optional[str] = None
    medicationReference: Reference
    basedOn: List[Reference] = Field(default_factory=list)
    effectivePeriod: Optional[Period] = None
    note: List[Annotation] = Field(default_factory=list)
    dosage: List[Dosage] = Field(default_factory=list)
