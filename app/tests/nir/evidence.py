"""Capture sandbox wire evidence without changing the application's HTTP client."""

import json
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
from defusedxml import ElementTree


def write_payload(prefix: Path, content: bytes, content_type: str) -> Path:
    """Keep exact wire bytes and expose XML roots of multipart messages."""

    def is_xml(data):
        try:
            ElementTree.fromstring(data)
            return True
        except Exception:
            return False

    if is_xml(content):
        path = Path(f"{prefix}.xml")
    elif content_type.lower().startswith("multipart/"):
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + content
        )
        path = Path(f"{prefix}.mime") if message.is_multipart() else Path(f"{prefix}.bin")
        if message.is_multipart():
            parts = []
            for index, part in enumerate(message.walk()):
                if part.is_multipart():
                    continue
                data = part.get_payload(decode=True)
                if data is None:
                    continue
                extension = (
                    "xml"
                    if is_xml(data)
                    else {"application/pdf": "pdf", "application/dicom": "dcm", "text/plain": "txt"}.get(
                        part.get_content_type(), "bin"
                    )
                )
                attachment = Path(f"{prefix}.part-{index:02d}.{extension}")
                attachment.write_bytes(data)
                parts.append(
                    {
                        "file": attachment.name,
                        "content_id": part.get("Content-ID"),
                        "content_type": part.get_content_type(),
                    }
                )
            Path(f"{prefix}.parts.json").write_text(json.dumps(parts, indent=2))
    else:
        path = Path(f"{prefix}.bin")
    path.write_bytes(content)
    return path


class EvidenceTransport(httpx.AsyncBaseTransport):
    """Record synthetic sandbox exchanges, including non-2xx responses."""

    def __init__(self, directory: Path, transport=None):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.transport = transport if transport is not None else httpx.AsyncHTTPTransport()
        self.count = 0

    async def handle_async_request(self, request):
        self.count += 1
        prefix = self.directory / f"{self.count:02d}"
        write_payload(prefix.with_suffix(".request"), await request.aread(), request.headers.get("content-type", ""))
        metadata = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "method": request.method,
            "url": str(request.url),
            "request_content_type": request.headers.get("content-type"),
        }
        try:
            response = await self.transport.handle_async_request(request)
            await response.aread()
            write_payload(prefix.with_suffix(".response"), response.content, response.headers.get("content-type", ""))
            metadata.update(
                status_code=response.status_code, response_content_type=response.headers.get("content-type")
            )
            return response
        except Exception as exc:
            metadata["transport_error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            prefix.with_suffix(".http.json").write_text(json.dumps(metadata, indent=2))

    async def aclose(self):
        await self.transport.aclose()
