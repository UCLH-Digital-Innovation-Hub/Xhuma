"""
FHIR to CCDA Conversion Module

This module handles the conversion of FHIR bundles to CCDA format,
with comprehensive logging and tracing of the conversion process.
"""

import datetime
from typing import Dict, List, Optional

from fhirclient.models import bundle
from fhirclient.models import list as fhirlist
from fhirclient.models import patient
from prometheus_client import Counter, Histogram
from opentelemetry import trace

from .entries import allergy, medication, problem
from .helpers import date_helper, templateId
from ..config import get_logger
from ..handlers import correlation_id_ctx_var

# Initialize logger
logger = get_logger("ccda")

# Initialize tracer
tracer = trace.get_tracer(__name__)

# Initialize metrics
CONVERSION_OPERATIONS = Counter(
    "ccda_conversion_operations_total",
    "Total count of CCDA conversion operations",
    ["operation", "status"]
)

SECTION_CONVERSION_DURATION = Histogram(
    "ccda_section_conversion_duration_seconds",
    "CCDA section conversion duration in seconds",
    ["section_type"]
)

async def convert_bundle(bundle_data: bundle.Bundle, index: dict) -> dict:
    """
    Convert FHIR bundle to CCDA format.
    
    :param bundle_data: FHIR bundle to convert
    :type bundle_data: bundle.Bundle
    :param index: Index of referenced resources
    :type index: dict
    :return: CCDA document as dictionary
    :rtype: dict
    """
    correlation_id = correlation_id_ctx_var.get(None)
    
    with tracer.start_as_current_span("convert_bundle") as span:
        try:
            logger.info(
                "Starting FHIR to CCDA conversion",
                extra={
                    "correlation_id": correlation_id,
                    "bundle_type": bundle_data.type,
                }
            )
            
            CONVERSION_OPERATIONS.labels(
                operation="start_conversion",
                status="success"
            ).inc()
            
            lists = [
                entry.resource
                for entry in bundle_data.entry
                if isinstance(entry.resource, fhirlist.List)
            ]
            subject = [
                entry.resource
                for entry in bundle_data.entry
                if isinstance(entry.resource, patient.Patient)
            ]
            
            if not subject:
                logger.error(
                    "No patient found in bundle",
                    extra={"correlation_id": correlation_id}
                )
                raise ValueError("No patient found in bundle")
            
            ccda = create_ccda_header(subject[0])
            
            # Add patient data
            with tracer.start_as_current_span("add_patient_data"):
                ccda["ClinicalDocument"]["recordTarget"] = create_patient_section(subject[0])
                logger.debug(
                    "Added patient data to CCDA",
                    extra={
                        "correlation_id": correlation_id,
                        "nhs_number": subject[0].identifier[0].value
                    }
                )
            
            # Add author data
            ccda["ClinicalDocument"]["author"] = create_author_section()
            
            # Add documentation
            ccda["ClinicalDocument"]["documentationOf"] = create_documentation_section(subject[0])
            
            # Handle vital signs
            vital_signs = fhirlist.List()
            vital_signs.title = "Vital Signs"
            lists.append(vital_signs)
            
            # Convert sections
            bundle_components = await convert_sections(lists, index, correlation_id)
            
            ccda["ClinicalDocument"]["component"] = {
                "structuredBody": {
                    "component": bundle_components
                }
            }
            
            logger.info(
                "Completed FHIR to CCDA conversion",
                extra={
                    "correlation_id": correlation_id,
                    "sections_converted": len(bundle_components)
                }
            )
            
            CONVERSION_OPERATIONS.labels(
                operation="complete_conversion",
                status="success"
            ).inc()
            
            return ccda
            
        except Exception as e:
            logger.error(
                f"Error during CCDA conversion: {str(e)}",
                extra={
                    "correlation_id": correlation_id,
                    "error": str(e)
                }
            )
            
            CONVERSION_OPERATIONS.labels(
                operation="complete_conversion",
                status="failure"
            ).inc()
            
            span.record_exception(e)
            raise

def create_ccda_header(subject: patient.Patient) -> Dict:
    """
    Create the CCDA document header.
    
    :param subject: Patient resource
    :type subject: patient.Patient
    :return: CCDA header dictionary
    :rtype: Dict
    """
    return {
        "ClinicalDocument": {
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@xmlns": "urn:hl7-org:v3",
            "@xmlns:voc": "urn:hl7-org:v3/voc",
            "@xmlns:sdtc": "urn:hl7-org:sdtc",
            "realmCode": {"@code": "GB"},
            "typeId": {"@root": "2.16.840.1.113883.1.3", "@extension": "POCD_HD000040"},
            "templateId": templateId("2.16.840.1.113883.10.20.22.1.2", "2015-08-01"),
            "code": {
                "@code": "34133-9",
                "@codeSystem": "2.16.840.1.113883.6.1",
            },
            "title": {"#text": "Summary Care Record"}
        }
    }

def create_patient_section(subject: patient.Patient) -> Dict:
    """
    Create the patient section of the CCDA document.
    
    :param subject: Patient resource
    :type subject: patient.Patient
    :return: Patient section dictionary
    :rtype: Dict
    """
    return {
        "patientRole": {
            "id": {
                "@extension": subject.identifier[0].value,
                "@root": "2.16.840.1.113883.2.1.4.1",
            },
            "addr": {
                "@use": "HP",
                "streetAddressLine": [x for x in subject.address[0].line],
                "city": {"#text": subject.address[0].city},
                "postalCode": {"#text": subject.address[0].postalCode},
            },
            "patient": {
                "name": {
                    "@use": "L",
                    "given": {"#text": " ".join(subject.name[0].given)},
                    "family": {"#text": subject.name[0].family},
                },
                "birthTime": {"@value": date_helper(subject.birthDate.isostring)},
            },
        }
    }

def create_author_section() -> Dict:
    """
    Create the author section of the CCDA document.
    
    :return: Author section dictionary
    :rtype: Dict
    """
    return {
        "time": {"@value": datetime.date.today().strftime("%Y%m%d")},
        "assignedAuthor": {
            "addr": {"@nullFlavor": "NA"},
            "telecom": {"@nullFlavor": "NA"},
            "assignedAuthoringDevice": {
                "manufacturerModelName": {"#text": "SCR Connector"},
                "softwareName": {"#text": "SCR Connector v0.1"},
            },
        },
    }

def create_documentation_section(subject: patient.Patient) -> Dict:
    """
    Create the documentation section of the CCDA document.
    
    :param subject: Patient resource
    :type subject: patient.Patient
    :return: Documentation section dictionary
    :rtype: Dict
    """
    return {
        "serviceEvent": {
            "@classCode": "PCPR",
            "effectiveTime": {
                "low": {
                    "@value": date_helper(subject.birthDate.isostring),
                },
                "high": {"@value": datetime.date.today().strftime("%Y%m%d")},
            },
        }
    }

async def convert_sections(lists: List[fhirlist.List], index: dict, 
                         correlation_id: Optional[str]) -> List[Dict]:
    """
    Convert FHIR lists to CCDA sections.
    
    :param lists: List of FHIR List resources
    :type lists: List[fhirlist.List]
    :param index: Index of referenced resources
    :type index: dict
    :param correlation_id: Correlation ID for tracing
    :type correlation_id: Optional[str]
    :return: List of converted CCDA sections
    :rtype: List[Dict]
    """
    templates = {
        "Allergies and adverse reactions": {
            "displayName": "Allergies, adverse reactions, alerts",
            "root": "2.16.840.1.113883.10.20.22.2.6.1",
            "Code": "48765-2",
        },
        "Medications and medical devices": {
            "displayName": "Medications",
            "root": "2.16.840.1.113883.10.20.22.2.1",
            "Code": "10160-0",
        },
        "Problems": {
            "displayName": "Problems List",
            "root": "2.16.840.1.113883.10.20.22.2.5.1",
            "Code": "11450-4",
        },
        "Immunisations": {
            "displayName": "Immunisations",
            "root": "2.16.840.1.113883.10.20.22.2.5",
            "Code": "11450-4",
        },
        "Vital Signs": {
            "displayName": "Vital Signs",
            "root": "2.16.840.1.113883.10.20.22.2.4.1",
            "Code": "8716-3",
        },
    }

    sections = [
        "Allergies and adverse reactions",
        "Immunisations",
        "Medications and medical devices",
        "Problems",
        "Vital Signs",
    ]

    bundle_components = []
    
    for list_item in lists:
        if list_item.title in sections:
            with tracer.start_as_current_span(f"convert_section_{list_item.title}") as span:
                with SECTION_CONVERSION_DURATION.labels(
                    section_type=list_item.title
                ).time():
                    try:
                        logger.debug(
                            f"Converting section: {list_item.title}",
                            extra={
                                "correlation_id": correlation_id,
                                "section": list_item.title,
                                "entry_count": len(list_item.entry) if list_item.entry else 0
                            }
                        )
                        
                        comp = create_section_component(list_item, templates, index)
                        if comp:
                            bundle_components.append(comp)
                            
                            CONVERSION_OPERATIONS.labels(
                                operation=f"convert_section_{list_item.title}",
                                status="success"
                            ).inc()
                            
                    except Exception as e:
                        logger.error(
                            f"Error converting section {list_item.title}: {str(e)}",
                            extra={
                                "correlation_id": correlation_id,
                                "section": list_item.title,
                                "error": str(e)
                            }
                        )
                        
                        CONVERSION_OPERATIONS.labels(
                            operation=f"convert_section_{list_item.title}",
                            status="failure"
                        ).inc()
                        
                        span.record_exception(e)
                        raise
    
    return bundle_components

def create_section_component(list_item: fhirlist.List, templates: Dict, index: Dict) -> Optional[Dict]:
    """
    Create a CCDA section component from a FHIR List.
    
    :param list_item: FHIR List resource
    :type list_item: fhirlist.List
    :param templates: Templates for section creation
    :type templates: Dict
    :param index: Index of referenced resources
    :type index: Dict
    :return: CCDA section component or None
    :rtype: Optional[Dict]
    """
    comp = {
        "section": {
            "templateId": templateId(templates[list_item.title]["root"], "2015-8-1"),
            "code": {
                "@code": templates[list_item.title]["Code"],
                "@displayName": templates[list_item.title]["displayName"],
                "@codeSystem": "2.16.840.1.113883.6.1",
            },
            "title": list_item.title,
            "text": "lorem ipsum",
        }
    }
    
    if not list_item.entry:
        comp["section"]["text"] = "No Information"
    else:
        comp["section"]["entry"] = []
        for entry in list_item.entry:
            referenced_item = index[entry.item.reference]
            
            if list_item.title == "Allergies and adverse reactions":
                comp["section"]["entry"].append(allergy(referenced_item))
            elif list_item.title == "Problems":
                comp["section"]["entry"].append(problem(referenced_item))
            elif list_item.title == "Medications and medical devices":
                comp["section"]["entry"].append(medication(referenced_item, index))
    
    return comp
