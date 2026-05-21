import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pds.pds import lookup_patient


@patch("app.pds.pds.redis_client")
@patch("app.pds.pds.httpx.post")
@patch("app.pds.pds.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_get_data_success(mock_async_client, mock_post, mock_redis):
    # --- mock redis: no token exists ---
    mock_redis.exists.return_value = False
    mock_redis.setex.return_value = True  # avoid failure

    # --- mock token response ---
    mock_post.return_value.text = json.dumps(
        {"access_token": "fake-token", "expires_in": 300}
    )

    # --- mock patient response ---
    mock_client_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps({"resourceType": "Patient", "id": "9690937278"})

    mock_client_instance.get.return_value = mock_response
    mock_async_client.return_value.__aenter__.return_value = mock_client_instance

    patient = await lookup_patient(9690937278)

    assert patient["resourceType"] == "Patient"
    assert patient["id"] == "9690937278"
