# Linux amd64 / Python 3.12 dependency lock. Pin PYTHON_IMAGE to an approved digest for releases.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
FROM ${PYTHON_IMAGE} AS local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    GRADIO_ANALYTICS_ENABLED=False HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    APP_HOST=0.0.0.0 PORT=7860 APP_LLM_MODE=mock
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip check \
    && useradd --create-home --uid 10001 demo
COPY app.py main_UI.py config.py ./
COPY src ./src
COPY prompts ./prompts
COPY synthetic_demo ./synthetic_demo
COPY data/synthetic ./data/synthetic
COPY data/icon.png ./data/icon.png
RUN mkdir -p var/synthetic var/diagnostics \
    && python -m synthetic_demo.seed sqlite --output var/synthetic/maintenance_v1.sqlite3 \
    && chown -R demo:demo /app/var
USER 10001
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=3s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health/ready', timeout=2)"
CMD ["python", "app.py"]

# Optional Azure transport OS driver. Building this target downloads Microsoft packages
# and accepts the ODBC Driver EULA; it never provisions Azure or connects to a database.
FROM local AS azure
USER root
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl --fail --silent --show-error --location https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -o /tmp/msprod.deb \
    && dpkg -i /tmp/msprod.deb && rm /tmp/msprod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
USER 10001
