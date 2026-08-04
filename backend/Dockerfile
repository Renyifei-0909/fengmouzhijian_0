FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /src

COPY uv-bootstrap.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r uv-bootstrap.txt

COPY pyproject.toml uv.lock build-constraints.txt README.md MANIFEST.in alembic.ini ./
COPY scripts/verify_dependency_lock.py ./scripts/verify_dependency_lock.py
RUN python scripts/verify_dependency_lock.py \
    && uv lock --check --no-python-downloads \
    && uv sync --locked --no-install-project --no-python-downloads

COPY app ./app
RUN uv build --wheel \
        --build-constraints build-constraints.txt \
        --require-hashes \
        --no-python-downloads \
        --out-dir /tmp/dist \
    && uv pip install \
        --python /opt/venv/bin/python \
        --no-deps \
        --no-python-downloads \
        /tmp/dist/*.whl \
    && uv pip check --python /opt/venv/bin/python --no-python-downloads

FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /src/alembic.ini ./alembic.ini

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
