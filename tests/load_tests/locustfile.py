import uuid
from locust import HttpUser, task, between

def generate_soap_payload(nhs_number: str) -> str:
    """Generates a mock SOAP envelope containing an ITI-55 request"""
    # This is a simplified ITI-55 structure for benchmarking the CPU endpoints
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://www.w3.org/2005/08/addressing">
    <s:Header>
        <a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID>
        <a:Action s:mustUnderstand="1">urn:hl7-org:v3:PRPA_IN201305UV02:CrossGatewayPatientDiscovery</a:Action>
    </s:Header>
    <s:Body>
        <PRPA_IN201305UV02 xmlns="urn:hl7-org:v3">
            <controlActProcess classCode="CACT" moodCode="EVN">
                <queryByParameter>
                    <parameterList>
                        <livingSubjectId>
                            <value root="2.16.840.1.113883.2.1.4.1" extension="{nhs_number}"/>
                        </livingSubjectId>
                    </parameterList>
                </queryByParameter>
            </controlActProcess>
        </PRPA_IN201305UV02>
    </s:Body>
</s:Envelope>"""

class EpicClientUser(HttpUser):
    # Wait between 1 and 3 seconds between tasks to mimic real-world pacing
    wait_time = between(1, 3)

    @task(3)
    def test_iti_55_patient_discovery(self):
        """Simulates an ITI-55 Patient Discovery lookup"""
        payload = generate_soap_payload("9692136744")
        
        # We use a Multipart MIME boundary since Xhuma expects MTOM SOAP
        boundary = "uuid:benchmark-boundary"
        headers = {
            "Content-Type": f'multipart/related; type="application/xop+xml"; boundary="{boundary}"'
        }
        
        body = f"""--{boundary}\r
Content-Type: application/xop+xml; charset=UTF-8; type="application/soap+xml"\r
\r
{payload}\r
--{boundary}--\r
"""
        self.client.post("/SOAP/iti55", data=body, headers=headers, name="/SOAP/iti55 (Patient Discovery)")

    @task(1)
    def test_iti_38_document_query(self):
        """Simulates an ITI-38 Document Query (Using the same payload structure for benchmark routing)"""
        payload = generate_soap_payload("9692136744")
        boundary = "uuid:benchmark-boundary"
        headers = {
            "Content-Type": f'multipart/related; type="application/xop+xml"; boundary="{boundary}"'
        }
        body = f"--{boundary}\\r\\nContent-Type: application/xop+xml; charset=UTF-8; type=\"application/soap+xml\"\\r\\n\\r\\n{payload}\\r\\n--{boundary}--\\r\\n"
        self.client.post("/SOAP/iti38", data=body, headers=headers, name="/SOAP/iti38 (Document Query)")
