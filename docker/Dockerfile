FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY scripts/ ./scripts/

RUN pip install uv && uv pip install --system --require-hashes -r pyproject.toml