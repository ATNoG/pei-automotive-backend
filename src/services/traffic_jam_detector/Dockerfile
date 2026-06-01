FROM python:3.11-slim

WORKDIR /app

COPY src/common ./common
COPY src/services/traffic_jam_detector/service.py .

COPY docker_requirements.txt .
RUN pip install --no-cache-dir -r docker_requirements.txt

CMD ["python", "service.py"]