# Independent application image; never inherit another project's application image.
ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /opt/news-assistant

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/data /app/workspace \
    && chown -R app:app /opt/news-assistant /app/data /app/workspace

COPY --chown=10001:10001 . /opt/news-assistant

USER 10001:10001
CMD ["python", "scripts/check_design.py"]
