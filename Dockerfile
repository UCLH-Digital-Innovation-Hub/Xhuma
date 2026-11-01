# xmltodict-fast 1.1.0 has no Linux amd64 wheel. Compile its Rust extension
# in a build stage; compilers and Cargo do not belong in the runtime image.
FROM python:3.13-slim AS dependencies

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /code
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cargo rustc && \
    rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock /code/
RUN uv sync --locked --no-dev --no-install-project && \
    .venv/bin/python -c 'import xmltodict; assert xmltodict._RUST_AVAILABLE'

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /code

RUN apt-get update && apt-get upgrade -y && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    python -m pip uninstall -y pip setuptools

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /code/

COPY --from=dependencies /code/.venv /code/.venv

COPY app /code/app
COPY alembic.ini /code/alembic.ini
COPY alembic /code/alembic
COPY startup.sh /code/startup.sh

ENV PATH="/code/.venv/bin:$PATH"

CMD ["./startup.sh"]
