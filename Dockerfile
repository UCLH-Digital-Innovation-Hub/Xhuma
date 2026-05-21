FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /code

RUN apt-get update && apt-get upgrade -y && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /code/

RUN uv sync --locked --no-dev --no-install-project

COPY app /code/app
COPY alembic.ini /code/alembic.ini
COPY alembic /code/alembic
COPY startup.sh /code/startup.sh

ENV PATH="/code/.venv/bin:$PATH"

CMD ["./startup.sh"]
