# Builder stage: compile Mosquitto from source with WebSocket support enabled.
FROM debian:bookworm-slim AS mosquitto-builder

ARG MOSQUITTO_VERSION=2.0.18

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        wget \
        ca-certificates \
        libssl-dev \
        libwebsockets-dev \
        libcjson-dev \
        uuid-dev \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q "https://mosquitto.org/files/source/mosquitto-${MOSQUITTO_VERSION}.tar.gz" \
 && tar xzf "mosquitto-${MOSQUITTO_VERSION}.tar.gz" \
 && cd "mosquitto-${MOSQUITTO_VERSION}" \
 && make WITH_WEBSOCKETS=yes WITH_DOCS=no WITH_CJSON=yes -j"$(nproc)" \
 && cp src/mosquitto /tmp/mosquitto.ws

# Final stage: inherit the iegomez image (go-auth.so plugin, /mosquitto/pw tool,
# entrypoint conventions, mosquitto user) and swap in the WS-enabled binary.
FROM iegomez/mosquitto-go-auth:3.0.0-mosquitto_2.0.18

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        libwebsockets17 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mosquitto-builder /tmp/mosquitto.ws /usr/sbin/mosquitto
RUN chmod +x /usr/sbin/mosquitto
