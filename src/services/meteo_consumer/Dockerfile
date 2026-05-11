FROM python:3.11-slim

WORKDIR /app

COPY docker_requirements.txt .

RUN pip install --no-cache-dir -r docker_requirements.txt

COPY src/common /app/common
COPY src/services/meteo_consumer /app/meteo_consumer

CMD ["python", "meteo_consumer/service.py"]