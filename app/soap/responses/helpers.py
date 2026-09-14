from datetime import datetime, timedelta


def create_security():
    current_time = datetime.now()
    expiration_time = current_time + timedelta(minutes=5)

    current_timestamp = current_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    expiration_timestamp = expiration_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    security = {
        "@s:mustUnderstand": 1,
        "@xmlns:o": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
        "u:Timestamp": {
            "@u:Id": "_0",
            "u:Created": {"#text": current_timestamp},
            "u:Expires": {"#text": expiration_timestamp},
        },
    }

    return security


def create_header(message_urn: str, message_id: str):
    header = {
        "a:Action": {
            "@s:mustUnderstand": 1,
            "#text": message_urn,
        },
        "a:RelatesTo": {"#text": message_id},
        # "o:Security": create_security(),
    }
    return header


def create_envelope(header, body):
    envelope = {
        "s:Envelope": {
            "@xmlns:s": "http://www.w3.org/2003/05/soap-envelope",
            "@xmlns:a": "http://www.w3.org/2005/08/addressing",
            "@xmlns:u": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
            "s:Header": header,
            "s:Body": body,
        }
    }
    return envelope


def create_id(root, extension):
    return {"@root": root, "@extension": extension}
