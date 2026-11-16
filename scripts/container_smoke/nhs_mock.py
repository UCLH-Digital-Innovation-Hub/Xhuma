"""HTTP service doubles only; Xhuma's runtime, Redis and database remain real."""

import json
import ssl
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PATIENT = "9690937286"
CALLS = Counter()
LOCK = threading.Lock()


def identifier(name, value):
    return {"system": f"https://fhir.nhs.uk/Id/{name}", "value": value}


class NHSHandler(BaseHTTPRequestHandler):
    def reply(self, status, body, operation=None):
        if operation:
            with LOCK:
                CALLS[operation] += 1
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == "/health":
            return self.reply(200, {"status": "ok"})
        if url.path == "/calls":
            with LOCK:
                calls = dict(CALLS)
            return self.reply(200, calls)
        if url.path == f"/personal-demographics/FHIR/R4/Patient/{PATIENT}":
            patient = json.loads(Path(f"/fixtures/pdsresults/{PATIENT}.json").read_text())
            return self.reply(200, patient, "pds")
        if url.path == "/spine-directory/FHIR/R4/Device":
            consumer = "manufacturing-organization" in query
            resource = {
                "resourceType": "Device",
                "identifier": [
                    identifier("nhsSpineASID", "123" if consumer else "456"),
                    identifier("nhsMhsPartyKey", "consumer-party" if consumer else "provider-party"),
                ],
            }
            return self.reply(
                200,
                {"resourceType": "Bundle", "entry": [{"resource": resource}]},
                "sds-consumer-device" if consumer else "sds-provider-device",
            )
        if url.path == "/spine-directory/FHIR/R4/Endpoint":
            consumer = any("consumer-party" in value for value in query.get("identifier", []))
            resource = {
                "resourceType": "Endpoint",
                "status": "active",
                "identifier": [identifier("nhsMhsPartyKey", "consumer-party" if consumer else "provider-party")],
                "address": "https://consumer.smoke.invalid/spine"
                if consumer
                else "https://provider.smoke.invalid/fhir",
            }
            return self.reply(
                200,
                {"resourceType": "Bundle", "entry": [{"resource": resource}]},
                "sds-consumer-endpoint" if consumer else "sds-provider-endpoint",
            )
        if url.path == "/production1/fhir/CodeSystem/$lookup" and query.get("code"):
            return self.reply(
                200,
                {"resourceType": "Parameters", "parameter": [{"name": "display", "valueString": "Smoke medication"}]},
                "terminology",
            )
        self.reply(404, {"error": "Unexpected mock GET"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path in (
            "/oauth2/token",
            "/authorisation/auth/realms/nhs-digital-terminology/protocol/openid-connect/token",
        ):
            operation = "pds-token" if self.path == "/oauth2/token" else "terminology-token"
            return self.reply(200, {"access_token": "smoke-token", "expires_in": 300}, operation)
        if self.path == "/send/client1":
            request = json.loads(body)
            parameters = json.loads(request["body"])["parameter"]
            valid = (
                request["method"] == "POST"
                and request["url"].endswith("/Patient/$gpc.getstructuredrecord")
                and any(
                    p.get("name") == "patientNHSNumber" and p.get("valueIdentifier", {}).get("value") == PATIENT
                    for p in parameters
                )
            )
            if not valid:
                return self.reply(400, {"error": "Unexpected GP Connect request"})
            bundle = json.loads(Path(f"/fixtures/bundles/{PATIENT}.json").read_text())
            # This fixture has text-only doses. Add one structured test dose so
            # the journey also exercises dm+d HTTP parsing and Redis caching.
            medication = next(
                entry["resource"]
                for entry in bundle["entry"]
                if entry["resource"]["resourceType"] == "MedicationStatement"
            )
            medication["dosage"][0]["doseQuantity"] = {"value": 1, "unit": "tablet"}
            return self.reply(200, {"status_code": 200, "body": json.dumps(bundle)}, "gpconnect")
        self.reply(404, {"error": "Unexpected mock POST"})


def serve():
    http = ThreadingHTTPServer(("0.0.0.0", 8080), NHSHandler)
    https = ThreadingHTTPServer(("0.0.0.0", 443), NHSHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/state/cert.pem", "/state/key.pem")
    https.socket = context.wrap_socket(https.socket, server_side=True)
    threading.Thread(target=https.serve_forever, daemon=True).start()
    http.serve_forever()


if __name__ == "__main__":
    serve()
