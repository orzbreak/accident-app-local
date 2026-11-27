FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ACCIDENT_DATA_DIR=/var/lib/accident-app ACCIDENT_LOG_MODEL_RESPONSES=0
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --user-group appuser \
    && install -d -o appuser -g appuser /var/lib/accident-app
COPY app.py .
COPY services/ services/
COPY static/ static/
COPY fonts/ fonts/

USER appuser
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
