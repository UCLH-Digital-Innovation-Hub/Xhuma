from typing import Optional, List

from pydantic import Field, field_validator

from .base import FHIRBaseModel, Identifier, Meta, Extension, CodeableConcept, Reference, Annotation


class AllergyIntoleranceReaction(FHIRBaseModel):
    manifestation: List[CodeableConcept]


class AllergyIntolerance(FHIRBaseModel):
    resourceType: str = Field(default="AllergyIntolerance")
    id: Optional[str] = None
    meta: Optional[Meta] = None
    identifier: List[Identifier] = Field(default_factory=list)
    extension: List[Extension] = Field(default_factory=list)
    
    clinicalStatus: Optional[str] = None
    verificationStatus: Optional[str] = None
    
    # Asserted Date is heavily used in CCDA conversion. FHIR allows partial dates (e.g. YYYY-MM)
    assertedDate: str
    
    # Code is critical for SCAL data safety
    code: CodeableConcept
    
    patient: Reference
    recorder: Optional[Reference] = None
    
    note: List[Annotation] = Field(default_factory=list)
    reaction: List[AllergyIntoleranceReaction] = Field(default_factory=list)

    @field_validator('code')
    @classmethod
    def validate_code_has_snomed(cls, v: CodeableConcept) -> CodeableConcept:
        if not v.coding:
            raise ValueError("AllergyIntolerance.code must contain at least one Coding element")
        
        # Check if at least one coding has SNOMED CT system
        has_snomed = any(c.system == "http://snomed.info/sct" for c in v.coding)
        
        if not has_snomed:
            # Check for generic nullFlavor or text if we want to allow non-SNOMED (but SCAL technically rejects this)
            raise ValueError("AllergyIntolerance.code must contain a valid SNOMED CT coding (http://snomed.info/sct). Transfer-degraded data must use a fallback SNOMED code.")
            
        return v
