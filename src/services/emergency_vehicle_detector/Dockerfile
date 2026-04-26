FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY src/common ./common
COPY src/services/emergency_vehicle_detector/service.py .

COPY docker_requirements.txt .
RUN pip install --no-cache-dir -r docker_requirements.txt

CMD ["python", "service.py"]
