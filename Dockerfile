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

COPY app /code/app

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
