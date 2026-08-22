FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py catalog.py ./

ENV PYTHONUNBUFFERED=1

# Cloud Run sets $PORT; gunicorn binds to it. Each request just calls the
# Stripe API and returns, so 2 workers is plenty for a small shop.
CMD exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 30 app:app
