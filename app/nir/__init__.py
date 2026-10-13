"""National Imaging Registry consumer integration.

Import ``NIRClient`` and ``NIRConfig`` here to use the consumer independently of
the FastAPI routes. The IHE request and response models live in ``app.soap``.
"""

from .client import NIRClient
from .config import NIRConfig

__all__ = ["NIRClient", "NIRConfig"]
