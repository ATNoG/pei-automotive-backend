#!/usr/bin/env bash
# Probe Ditto connectivity over HTTP (REST) and WS (upgrade handshake)
# via both the internal NodePort IP and the public DDNS hostname.
# checks:
# HTTP via IP                  http://10.255.28.243:31256/api/2/things  ->  HTTP 200
# HTTP via domain              http://automotive-app.ddns.net/api/2/things  ->  HTTP 200
# WS   via IP                  http://10.255.28.243:31256/ws/2  ->  HTTP 101 (WS OK)
# WS   via domain              http://automotive-app.ddns.net/ws/2  ->  HTTP 400


set -u

IP_HOST="10.255.28.243:31256"
DOMAIN_HOST="automotive-app.ddns.net"
USER="${DITTO_USER:-ditto}"
PASS="${DITTO_PASS:-ditto}"

probe_http() {
    local label="$1" url="$2"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" -u "$USER:$PASS" -m 5 "$url/api/2/things")
    printf "%-28s %s  ->  HTTP %s\n" "$label" "$url/api/2/things" "$code"
}

probe_ws() {
    local label="$1" url="$2"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" -u "$USER:$PASS" -m 5 --http1.1 \
        -H "Upgrade: websocket" \
        -H "Connection: Upgrade" \
        -H "Sec-WebSocket-Version: 13" \
        -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
        "$url/ws/2")
    # 101 = Switching Protocols = WS upgrade accepted
    printf "%-28s %s  ->  HTTP %s%s\n" "$label" "$url/ws/2" "$code" \
        "$([ "$code" = "101" ] && echo " (WS OK)" || echo "")"
}

echo "=== Ditto connectivity probe ==="
probe_http "HTTP via IP"      "http://$IP_HOST"
probe_http "HTTP via domain"  "http://$DOMAIN_HOST"
probe_ws   "WS   via IP"      "http://$IP_HOST"
probe_ws   "WS   via domain"  "http://$DOMAIN_HOST"
