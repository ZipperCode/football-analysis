ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

COPY pyproject.toml README.md ./
RUN python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; open('/tmp/requirements.txt', 'w', encoding='utf-8').write('\n'.join(deps) + '\n')"
RUN pip install --timeout 60 --retries 5 -r /tmp/requirements.txt

COPY src ./src
COPY config ./config

RUN pip install --timeout 60 --retries 5 --no-deps .

EXPOSE 8000

CMD ["uvicorn", "football_analysis.api:app", "--host", "0.0.0.0", "--port", "8000"]
