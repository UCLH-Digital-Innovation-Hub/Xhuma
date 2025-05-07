FROM python:3.13

WORKDIR /code

RUN pip install pipenv

COPY Pipfile /code/Pipfile
COPY Pipfile.lock /code/Pipfile.lock

RUN pipenv install --system --deploy

COPY app /code/app

# Note: JWTKEY should be passed as an environment variable at runtime
# Do not store sensitive keys in the image
CMD uvicorn app.main:app --host=0.0.0.0 --port=${PORT:-80}
