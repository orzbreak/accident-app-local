FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/glows-worker
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"
ENV GLOWS_YOLO_MODEL=/opt/glows-worker/yolo11n.pt
COPY app.py .
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
