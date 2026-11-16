"""SOAP 1.2 faults shared by request validation and exception handlers."""

import xmltodict
from fastapi import HTTPException, Response

SOAP_NAMESPACE = "http://www.w3.org/2003/05/soap-envelope"
ADDRESSING_NAMESPACE = "http://www.w3.org/2005/08/addressing"
SECURITY_NAMESPACE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"


class SoapFault(HTTPException):
    """A protocol fault whose HTTP status follows its SOAP fault code."""

    def __init__(
        self,
        reason: str,
        *,
        code: str = "Sender",
        subcode: str | None = None,
        message_id: str | None = None,
        problem_header: str | None = None,
    ):
        super().__init__(status_code=400 if code == "Sender" else 500, detail=reason)
        self.code = code
        self.subcode = subcode
        self.message_id = message_id
        self.problem_header = problem_header

    def response(self) -> Response:
        fault_code = {"env:Value": f"env:{self.code}"}
        if self.subcode:
            fault_code["env:Subcode"] = {"env:Value": self.subcode}
        fault = {
            "env:Code": fault_code,
            "env:Reason": {"env:Text": {"@xml:lang": "en", "#text": self.detail}},
        }
        if self.problem_header:
            fault["env:Detail"] = {"wsa:ProblemHeaderQName": self.problem_header}
        header = {"wsa:Action": f"{ADDRESSING_NAMESPACE}/fault"}
        if self.message_id:
            header["wsa:RelatesTo"] = self.message_id
        envelope = {
            "env:Envelope": {
                "@xmlns:env": SOAP_NAMESPACE,
                "@xmlns:wsa": ADDRESSING_NAMESPACE,
                "@xmlns:wsse": SECURITY_NAMESPACE,
                "env:Header": header,
                "env:Body": {"env:Fault": fault},
            }
        }
        return Response(
            content=xmltodict.unparse(envelope, pretty=True),
            status_code=self.status_code,
            media_type="application/soap+xml",
        )
