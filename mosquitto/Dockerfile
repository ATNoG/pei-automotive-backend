# Builder stage: compile Mosquitto from source with WebSocket support enabled.
FROM debian:bookworm-slim AS mosquitto-builder

ARG MOSQUITTO_VERSION=2.0.18

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        wget \
        ca-certificates \
        libssl-dev \
        libwebsockets-dev \
        libcjson-dev \
        uuid-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN wget -q "https://mosquitto.org/files/source/mosquitto-${MOSQUITTO_VERSION}.tar.gz" \
 && tar xzf "mosquitto-${MOSQUITTO_VERSION}.tar.gz"

WORKDIR /build/mosquitto-${MOSQUITTO_VERSION}
RUN make WITH_WEBSOCKETS=yes WITH_DOCS=no WITH_CJSON=yes -j"$(nproc)" \
 && cp src/mosquitto /tmp/mosquitto.ws

# Final stage: inherit the iegomez image (go-auth.so plugin, /mosquitto/pw tool,
# entrypoint conventions, mosquitto user) and swap in the WS-enabled binary.
FROM iegomez/mosquitto-go-auth:3.0.0-mosquitto_2.0.18

USER root
RUN apt-get clean \
 && rm -rf /var/lib/apt/lists/* /etc/apt/sources.list /etc/apt/sources.list.d \
 && mkdir -p /etc/apt/sources.list.d \
 && printf 'deb http://deb.debian.org/debian bookworm main\ndeb http://deb.debian.org/debian bookworm-updates main\ndeb http://deb.debian.org/debian-security bookworm-security main\n' > /etc/apt/sources.list

# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends libwebsockets17 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=mosquitto-builder /tmp/mosquitto.ws /usr/sbin/mosquitto
RUN chmod +x /usr/sbin/mosquitto

USER mosquitto
