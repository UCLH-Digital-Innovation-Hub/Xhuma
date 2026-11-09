FROM python:3.13-slim AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /code

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends cargo rustc build-essential && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    python -m pip uninstall -y pip setuptools

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /code/

RUN uv sync --locked --no-dev --no-install-project

ENV PATH="/code/.venv/bin:$PATH"

FROM dependencies AS smoke-driver

RUN uv sync --locked --dev --no-install-project
COPY scripts/container_smoke/check.py scripts/container_smoke/check.py
COPY tests/load_tests/locustfile.py tests/load_tests/locustfile.py

FROM dependencies AS production

COPY app /code/app
COPY alembic.ini /code/alembic.ini
COPY alembic /code/alembic
COPY startup.sh /code/startup.sh

CMD ["./startup.sh"]
