ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/data /app/workspace /opt/news-assistant \
    && chown -R app:app /app /opt/news-assistant

FROM python-base AS dependencies
WORKDIR /build
RUN pip install --no-cache-dir uv==0.12.7
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy UV_HTTP_MULTIPLEXING=false
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache --python /usr/local/bin/python

FROM dependencies AS check-dependencies
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --python /usr/local/bin/python

FROM python-base AS runtime
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /opt/news-assistant
COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=10001:10001 . .
USER 10001:10001
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS check
USER root
COPY --from=check-dependencies /opt/venv /opt/venv
USER 10001:10001
CMD ["python", "scripts/check_all.py"]
