#!/bin/bash
# c2e env

export DITTO_API_BASE_URL=http://10.255.28.243:8080
export C2E_RELEASE=c2e
export C2E_NS=cloud2edge
export C2E_TRUSTSTORE_PATH=/home/filipeviseu/UNI/PEI/pei-automotive-backends/c2e_hono_truststore.pem
export MQTT_ADAPTER_IP=10.255.28.243
export MQTT_ADAPTER_PORT_MQTTS=8883
export MOSQUITTO_OPTIONS="--cafile /home/filipeviseu/UNI/PEI/pei-automotive-backend/c2e_hono_truststore.pem --insecure"

echo "c2e environment variables loaded!"