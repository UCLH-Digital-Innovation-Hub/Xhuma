"""Assemble Xhuma's CDA header using one document generation timestamp."""

from datetime import datetime

from fhirclient.models.patient import Patient

from app.version import VERSION

from .demographics import patient_record_target
from .helpers import cda_time_bound, templateId
from .models.admin import AssignedAuthor, AuthoringDevice, AuthorParticipation
from .models.datatypes import AD, CE, CS, II, IVL_TS, IVXB_TS, ST, TEL, TS
from .models.header import ClinicalDocumentHeader, DocumentationOf, ServiceEvent


def document_header(subject: Patient, index: dict, *, generated_at: datetime) -> ClinicalDocumentHeader:
    day = generated_at.strftime("%Y%m%d")
    return ClinicalDocumentHeader(
        realmCode=CS(code="GB"),
        typeId=II(root="2.16.840.1.113883.1.3", extension="POCD_HD000040"),
        templateId=templateId("2.16.840.1.113883.10.20.22.1.2", "2015-08-01"),
        code=CE(code="34133-9", codeSystem="2.16.840.1.113883.6.1"),
        title=ST(text="GP Connect: Access Record Structured"),
        effectiveTime=TS(value=generated_at.strftime("%Y%m%d%H%M%S")),
        recordTarget=patient_record_target(subject, index),
        author=AuthorParticipation(
            time=TS(value=day),
            assignedAuthor=AssignedAuthor(
                id=[II(root=None, nullFlavor="UNK")],
                address=[AD(nullFlavor="NA")],
                telecom=[TEL(nullFlavor="NA")],
                assignedAuthoringDevice=AuthoringDevice(
                    manufacturerModelName="Xhuma",
                    softwareName=f"Xhuma v{VERSION}",
                ),
            ),
        ),
        documentationOf=DocumentationOf(
            serviceEvent=ServiceEvent(
                effectiveTime=IVL_TS(low=cda_time_bound(subject.birthDate), high=IVXB_TS(value=day)),
            ),
        ),
    )
