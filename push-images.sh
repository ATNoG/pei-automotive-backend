#!/bin/bash

REGISTRY="atnog-harbor.av.it.pt/pei-2025-automotive-app"
TAG="1.0"

services=(
  position_processor
  speed_detector
  overtaking_detector
  emergency_vehicle_detector
  highway_entry_detector
  accident_detector
  traffic_jam_detector
  meteo_consumer
  station_assigner
  database_api
)

echo "Logging in to Harbor..."
docker login atnog-harbor.av.it.pt || exit 1

for service in "${services[@]}"; do
  echo "----------------------------------------"
  echo "Building $service..."

  docker build -t $service:$TAG \
    -f src/services/$service/Dockerfile . || exit 1

  echo "Tagging $service..."
  docker tag $service:$TAG $REGISTRY/$service:$TAG || exit 1

  echo "Pushing $service..."
  docker push $REGISTRY/$service:$TAG || exit 1

done

echo "----------------------------------------"
echo "All images built and pushed successfully!"