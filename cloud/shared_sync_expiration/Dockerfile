FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt \
    && useradd --system --uid 10001 appuser

COPY expiration.py main.py shared_sync_expiration/
USER appuser
CMD ["python", "-m", "shared_sync_expiration.main"]
