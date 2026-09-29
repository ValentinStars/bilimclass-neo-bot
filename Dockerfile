FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY bilim_neo ./bilim_neo
RUN pip install --no-cache-dir . && useradd --system --uid 10001 --create-home neo && mkdir /app/data && chown neo:neo /app/data
USER neo
CMD ["python", "-m", "bilim_neo"]
