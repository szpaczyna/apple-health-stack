FROM python:3.15-slim

WORKDIR /app

# Install system dependencies for lxml (psycopg-binary bundles its own libpq)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py formatters.py ./
COPY sql/ ./sql/

# The code stays root-owned; the ingester only writes the extracted GPX routes.
RUN useradd --system --uid 10001 --no-create-home ingester \
    && install -d -o ingester -g ingester /app/export
USER ingester

# Export zip should be mounted at /app/export.zip
ENV ZIP_PATH=/app/export.zip
ENV PG_HOST=postgres
ENV PG_PORT=5432
ENV PG_USER=postgres
ENV PG_DB=health

CMD ["python", "app.py"]
