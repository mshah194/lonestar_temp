FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY invoice_core.py app.py logo_b64.txt ./
COPY templates ./templates

ENV PYTHONUNBUFFERED=1

# Cloud Run sets $PORT; gunicorn binds to it. 2 workers is plenty for a
# single-admin internal tool; each request is short-lived (PDF gen + email).
CMD exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 60 app:app
