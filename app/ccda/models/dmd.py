from typing import Optional

from pydantic import BaseModel

from .datatypes import CD


class VPIProperty(BaseModel):
    unit: str
    value: float


class DMDConcept(BaseModel):
    concept_id: int
    valueString: str
    vpi: Optional[VPIProperty] = None
    route: Optional[CD] = None

    def __str__(self):
        return f"DMDConcept(id={self.concept_id}, valueString='{self.valueString}', vpi={self.vpi}, route={self.route})"
