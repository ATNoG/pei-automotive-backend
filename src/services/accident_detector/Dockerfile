FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY src/common ./common
COPY src/services/accident_detector/service.py .

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "service.py"]
