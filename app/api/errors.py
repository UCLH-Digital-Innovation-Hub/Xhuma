import logging
import traceback
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fhirclient.models.operationoutcome import OperationOutcome, OperationOutcomeIssue

from ..audit.audit import AuditFailureException
from ..soap import soap


async def audit_failure_handler(request: Request, exc: AuditFailureException):
    trace_id = str(uuid.uuid4())
    # Explicitly do NOT log traceback to avoid leaking SQL parameters
    logging.error(f"AuditFailureException [TraceID: {trace_id}] at {request.url.path}: {exc}")

    path = request.url.path
    if path.startswith("/SOAP") or path.startswith("/iti") or "soap" in path.lower():
        return soap.SoapFault(f"Internal Server Error (TraceID: {trace_id})", code="Receiver").response()

    elif path.startswith("/FHIR") or path.startswith("/pds") or "fhir" in path.lower():
        issue = OperationOutcomeIssue()
        issue.severity = "fatal"
        issue.code = "exception"
        issue.diagnostics = f"An internal error occurred. TraceID: {trace_id}"
        outcome = OperationOutcome()
        outcome.issue = [issue]
        return JSONResponse(status_code=502, content=outcome.as_json())

    else:
        return JSONResponse(
            status_code=502,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 502,
                "detail": f"An unexpected error occurred. TraceID: {trace_id}",
            },
        )


async def global_exception_handler(request: Request, exc: Exception):
    trace_id = str(uuid.uuid4())
    logging.error(f"Unhandled Exception [TraceID: {trace_id}] at {request.url.path}: {exc}\n{traceback.format_exc()}")

    path = request.url.path
    if path.startswith("/SOAP") or path.startswith("/iti") or "soap" in path.lower():
        return soap.SoapFault(f"Internal Server Error (TraceID: {trace_id})", code="Receiver").response()

    elif path.startswith("/FHIR") or path.startswith("/pds") or "fhir" in path.lower():
        issue = OperationOutcomeIssue()
        issue.severity = "fatal"
        issue.code = "exception"
        issue.diagnostics = f"An internal error occurred. TraceID: {trace_id}"
        outcome = OperationOutcome()
        outcome.issue = [issue]
        return JSONResponse(status_code=500, content=outcome.as_json())

    else:
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": f"An unexpected error occurred. TraceID: {trace_id}",
            },
        )


def register_exception_handlers(app: FastAPI):
    soap.register_handlers(app)
    app.add_exception_handler(AuditFailureException, audit_failure_handler)
    app.add_exception_handler(Exception, global_exception_handler)
