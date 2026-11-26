import json

import httpx
import pytest

from .evidence import EvidenceTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 404, 500])
async def test_capture_preserves_binary_wire_and_error_status(tmp_path, status):
    transport = EvidenceTransport(
        tmp_path / "case",
        httpx.MockTransport(
            lambda request: httpx.Response(
                status, content=b"\x00\xffresponse", headers={"content-type": "multipart/related; boundary=test"}
            )
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post("https://sandbox.example/test", content=b"\x00\xffrequest")
    assert response.content == b"\x00\xffresponse"
    assert (tmp_path / "case/01.request.bin").read_bytes() == b"\x00\xffrequest"
    assert (tmp_path / "case/01.response.bin").read_bytes() == response.content
    metadata = json.loads((tmp_path / "case/01.http.json").read_text())
    assert metadata["status_code"] == status
    assert metadata["response_content_type"] == "multipart/related; boundary=test"


@pytest.mark.asyncio
async def test_capture_records_transport_failure(tmp_path):
    def fail(request):
        raise httpx.ConnectError("unavailable")

    transport = EvidenceTransport(tmp_path / "case", httpx.MockTransport(fail))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post("https://sandbox.example/test", content=b"request")
    assert "ConnectError" in json.loads((tmp_path / "case/01.http.json").read_text())["transport_error"]
    assert not (tmp_path / "case/01.response.bin").exists()


def test_capture_refuses_to_overwrite_evidence(tmp_path):
    with pytest.raises(FileExistsError):
        EvidenceTransport(tmp_path)


def test_xml_preserves_original_bytes(tmp_path):
    from .evidence import write_payload

    content = b'<?xml version="1.0"?><Envelope>  <Body/> </Envelope>'
    path = write_payload(tmp_path / "01.response", content, "application/soap+xml")
    assert path.name == "01.response.xml"
    assert path.read_bytes() == content
    assert not list(tmp_path.glob("*.bin"))


def test_mtom_exposes_xml_and_binary_without_changing_wire(tmp_path):
    from .evidence import write_payload

    content = (
        b"--test\r\nContent-Type: application/xop+xml\r\nContent-ID: <root>\r\n\r\n"
        b"<Envelope/>\r\n--test\r\nContent-Type: application/dicom\r\nContent-ID: <image>\r\n\r\n"
        b"\x00\xffimage\r\n--test--\r\n"
    )
    path = write_payload(tmp_path / "01.response", content, "multipart/related; boundary=test")
    assert path.suffix == ".mime"
    assert path.read_bytes() == content
    assert next(tmp_path.glob("*.xml")).read_bytes() == b"<Envelope/>"
    assert next(tmp_path.glob("*.dcm")).read_bytes() == b"\x00\xffimage"
    parts = json.loads(next(tmp_path.glob("*.parts.json")).read_text())
    assert [p["content_id"] for p in parts] == ["<root>", "<image>"]
