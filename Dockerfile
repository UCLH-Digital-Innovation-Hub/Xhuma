FROM python:3.13-slim

# Upgrade pip and system packages to reduce vulnerabilities
RUN apt-get update && apt-get upgrade -y && \
	pip install --upgrade pip && \
	apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /code

RUN pip install pipenv

COPY Pipfile /code/Pipfile
COPY Pipfile.lock /code/Pipfile.lock

RUN pipenv install --system --deploy
# Copy APP code
COPY app /code/app

# copy alembic files for migrations
COPY alembic.ini /code/alembic.ini
COPY alembic /code/alembic

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
