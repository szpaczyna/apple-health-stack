FROM python:3.15-slim AS build

WORKDIR /src

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip wheel --no-cache-dir --no-deps -w /wheels .

FROM python:3.15-slim

WORKDIR /app

# Install system dependencies for lxml (psycopg-binary bundles its own libpq)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# Bind-mount the wheel so it never lands in an image layer.
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-cache-dir /wheels/*.whl

# The code stays root-owned; the ingester only writes the extracted GPX routes.
RUN useradd --system --uid 10001 --no-create-home ingester
USER ingester

# Export zip should be mounted at /app/export.zip
ENV ZIP_PATH=/app/export.zip

ENTRYPOINT ["apple-health-ingester"]
