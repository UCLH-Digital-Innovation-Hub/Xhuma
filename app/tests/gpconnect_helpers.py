from unittest.mock import MagicMock


def get_mock_request():
    mock_request = MagicMock()
    mock_request.app.state.SessionLocal = MagicMock()
    mock_request.app.state.ccda_expiry_hours = 4.0
    return mock_request


def fake_sds_device_trace():
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                            "value": "200000000000",
                        },
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": "FAKE-PARTY-KEY",
                        },
                    ],
                }
            }
        ],
    }


def fake_sds_endpoint_trace():
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": "fake-gpconnect-endpoint",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": "FAKE-PARTY-KEY",
                        }
                    ],
                }
            }
        ],
    }
