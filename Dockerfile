FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    JK_INFO_DIR=/tmp/jk-info

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp/jk-info /tmp/jk-cache /app/img /app/static /secrets/info \
    && useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app /tmp/jk-info /tmp/jk-cache

USER appuser

CMD ["sh", "-c", "mkdir -p \"$JK_INFO_DIR\"; if [ -d /secrets/info ]; then cp -Rn /secrets/info/. \"$JK_INFO_DIR\"/ 2>/dev/null || true; fi; exec uvicorn backend_api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
