FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY bilim_neo ./bilim_neo
RUN pip install --no-cache-dir . && useradd --system --uid 10001 --create-home neo && mkdir /app/data && chown neo:neo /app/data
USER neo
CMD ["python", "-m", "bilim_neo"]
