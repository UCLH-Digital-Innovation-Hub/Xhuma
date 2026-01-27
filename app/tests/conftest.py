
import os
import pytest
from unittest import mock

# Generated dummy key for testing
TEST_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC/uxUPa862j8by
r+maHG8McrZlxcOaSFUKBC2rVfKQHZMmoLRhXIFnI5uUjjVt7QINAP46xLWKSXFv
Tnagli5okLFRK1xuOpNi7zSHqoMnc3wdP8hKfzaNZPt5dOdHrFtB5PQVjh4gaUe9
2J4Yyqks8ZLSXAg73jB7jQGRllD6WhY9/JNn89tBrpcnifsGfYslWnoVw3y5sG86
NywRBVmoTCII2ABxvzggdbnMETuqPSTlISLSLYzb636kwWC9DcTvQuecLT/40mAG
jWYgk/3WFBJAP/O/PzAq3F342XW6wOWeACZUsDxgVz1qFltAtouQdwieu0frcXGO
jOtMWSHLAgMBAAECggEAGw0LsyHMSACwGqSMKnSxjEmRB3noF5f1+5RMVzyonQG3
Nb99w7DYBxPEiyinTCug2vHVbLg9PtYO3Zvt68lUoecsRV3/TAQSsGY8fJmnqITI
MZ5CpZQeP8PMIT5HtVqRg5IIiBQFlyATgasS3z+Ur+UjiG94r+2w3HWwD3jsUu3t
VHeIon5718igBIxwHvLDwKYD+8efY1lB+wD3LdXebvphP0fvKfb6A/6uUOeUs5ct
Cyh0i73uFBRGPr7QQPbTj8CJL3p2CM/UTuMHVY0bpKgH1/Rnhi1QFQHwxma/Tyw2
vkZdU+chwbjnjR0h81wDwZ0J3uEdJKr+g8xGcb3cuQKBgQDpaloytRkQ8GTX3Ujl
yTBUxecvKWwdhzq+vw6Q5R6g9vdivmop4Ia38o4jnld1XQ8c+qwHs8XI9TqGJX9X
xlZCPiCJt8eyN732XUoPzRVe86pNp5gvG96lP4rJc/qfgLQNBOPT+yPeMUZLtEWJ
2t+U6UI4W4Bq/8r969JjZEpVGQKBgQDSSDXqFyT62A0iRu7gyouxyUl/LDJLozw3
uH+wGAj9TZ/hluSnoNFl5GDtKofhmGSDeEsra6LzCVvB8OTmGWQ62B0YIvMSNgeh
26uRx6rUfch0YEeLpNzjSNeElwu5XbyrWGtAhr9AKYnQePTZhh9qObaXweiJnnGN
YC1MD/kGgwKBgG7s7OqWJ9Bl/facIe2Z3A8dcc126PtrbZ6Cm4B+cjzawRw7E6bN
HhjC+4uvzy5mSC1kb2bFp2PmLqbboRXsmsemUL5CCXQHi45OLUkvFE3ojHGVCPyy
SO/NL76nEF7GwkpBnaf6/MO2NQr7I+TskD5rT94Klg/DeguMC5LScYTZAoGBAJmy
yiWGMTNgA4mQSz3nDeAu/heEGGcMsxEPO9VcXAW5XSkof4y+kQ9mtCnlslgEaUNn
A5qDHCGEL5s8FjLRUF9qYymnMf+XmlCGHYt6Y7TZE1Fsph69q+486boyJRGiICsl
f480bknZkq/cGSt9Syz7bijSRMOCGgF50OINsrk9AoGBAKgrO790Yzt4jQB3VNMb
gw2tVSdM4D7Ahm2MjTvApejf3aYA2IokGqiXEpoMCc/wXHdIel7zZu0tR9xFFY7F
luzizX9WR7+cvOZ9rPAqNRrWZmtCGnKG7WvuYCBlLVw8LdhrlOGxQBWqYw7/qld8
q/edqwBdaJXwGJsjBL/Epd2o
-----END PRIVATE KEY-----"""

@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """
    Automatically mock environment variables for all tests.
    """
    monkeypatch.setenv("JWTKEY", TEST_PRIVATE_KEY)
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "xhuma_test")
    monkeypatch.setenv("USE_RELAY", "false")
