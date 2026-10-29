# Development tools belong only to the HTTP driver, never the image under test.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /driver
ENV UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --dev --no-install-project
ENV PATH="/driver/.venv/bin:$PATH"
COPY scripts/container_smoke/check.py scripts/container_smoke/check.py
COPY tests/load_tests/locustfile.py tests/load_tests/locustfile.py
