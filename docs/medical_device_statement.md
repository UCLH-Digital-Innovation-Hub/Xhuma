# Regulatory Classification Statement: Medical Device Status

**Document Purpose:** 
This statement provides a formal regulatory justification for the classification of the Xhuma middleware service. It outlines why Xhuma does not meet the definition of a Software as a Medical Device (SaMD) under the UK Medical Devices Regulations 2002 (as amended) and guidance provided by the Medicines and Healthcare products Regulatory Agency (MHRA).

**System Overview:**
Xhuma is a stateless middleware application that facilitates interoperability between the Trust's Electronic Health Record (EHR) system (Epic) and national NHS APIs (GP Connect and PDS). 

---

## 1. MHRA Classification Criteria

Under MHRA guidance, software is only classified as a medical device if it has a specific "medical purpose." Software that is intended solely for the IT environment (such as transferring, storing, converting formats, or displaying data) is **not** a medical device, provided it does not alter the data in a way that provides a diagnostic or therapeutic benefit.

To determine medical device status, the following criteria must be assessed:
1. Does the software perform an action on data beyond simple storage, archival, communication, or simple search?
2. Does the software alter the representation of data for a specific medical purpose?
3. Does the software provide clinical decision support (e.g., calculating dosages, diagnosing conditions, flagging risks based on patient-specific parameters)?

## 2. Assessment of Xhuma

Xhuma performs the following functions:
- **Data Conduit (Communication):** It receives inbound queries from Epic and securely routes them to NHS APIs.
- **Format Conversion:** It translates modern JSON-based FHIR structures returned by GP Connect into XML-based C-CDA documents that the Epic EHR can natively render.
- **Stateless Orchestration:** It temporarily caches routing endpoints and discovery information purely for system performance, but does not persistently store clinical records.

Xhuma **does not** perform the following functions:
- **No Clinical Decision Support:** Xhuma does not analyze the GP Connect data to generate new clinical insights, trigger clinical alerts, or recommend treatments.
- **No Diagnostic or Therapeutic Calculation:** Xhuma does not alter the clinical meaning of the data it retrieves. The data presented to the clinician is a direct, structural mapping of the source data provided by the patient's GP practice.
- **No Independent Medical Purpose:** Xhuma serves an IT infrastructural purpose (interoperability and translation). It relies entirely on the clinical logic and decision-making capabilities of the human clinician and the endpoint EHR (Epic).

## 3. Conclusion

Based on the MHRA guidelines for standalone software, **Xhuma is strictly classified as a data transfer, format conversion, and communication tool.** 

Because Xhuma does not alter data to provide clinical decision support, generate diagnoses, or perform therapeutic calculations, it does not possess an independent medical purpose. Therefore, **Xhuma is not a medical device** and does not require UKCA or CE marking.

*Note: While exempt from Medical Device Regulations, Xhuma remains subject to NHS Health IT standards. A Clinical Risk Management Plan and Hazard Log have been completed in full compliance with **DCB0129** (Clinical Risk Management: its Application in the Manufacture of Health IT Systems) to ensure the technical translation of data remains clinically safe.*
