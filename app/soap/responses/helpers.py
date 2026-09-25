from datetime import datetime, timedelta

from ..models import Identifier, ResponseHeader, SecurityHeader, SecurityTimestamp, SoapEnvelope, TextElement


def create_security():
    current_time = datetime.now()
    expiration_time = current_time + timedelta(minutes=5)

    current_timestamp = current_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    expiration_timestamp = expiration_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    return SecurityHeader(
        timestamp=SecurityTimestamp(
            created=TextElement(text=current_timestamp),
            expires=TextElement(text=expiration_timestamp),
        )
    ).to_xml_dict()


def create_header(message_urn: str, message_id: str):
    return ResponseHeader.create(message_urn, message_id).to_xml_dict()


def create_envelope(header, body):
    return SoapEnvelope.create(header, body).to_xml_dict()


def create_id(root, extension):
    return Identifier(root=root, extension=extension).to_xml_dict()
