FROM python:3.11-slim

WORKDIR /app

COPY src/common ./common
COPY src/services/lane_merge_detector/service.py .
COPY simulations/roads ./roads

COPY docker_requirements.txt .
RUN pip install --no-cache-dir -r docker_requirements.txt

CMD ["python", "service.py"]
