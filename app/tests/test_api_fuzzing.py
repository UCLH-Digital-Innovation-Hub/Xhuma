import os
import schemathesis

# Ensure mTLS is disabled for the local fuzzing environment
os.environ["REQUIRE_MTLS"] = "false"
os.environ["JWTKEY"] = "MOCK_KEY_FOR_TESTS"
os.environ["API_KEY"] = "mock_key"
os.environ["USE_RELAY"] = "false"

from app.main import app
from app.security import verify_api_key


def mock_verify_api_key():
    return True


# Override the API key dependency
app.dependency_overrides[verify_api_key] = mock_verify_api_key

# Load OpenAPI schema directly from the ASGI app
schema = schemathesis.openapi.from_asgi("/openapi.json", app)


@schema.parametrize()
def test_api(case):
    """
    Automatically fuzzes all endpoints documented in the OpenAPI schema.
    It will generate random inputs and verify that the API does not crash (500)
    and conforms to the documented schema.
    """
    response = case.call(app=app)
    case.validate_response(response)
