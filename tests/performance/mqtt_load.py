#!/usr/bin/env python3
import argparse
import json
import statistics
import threading
import time
from dataclasses import dataclass, field
import paho.mqtt.client as mqtt

TOPIC = "cars/updates"


# Metrics

@dataclass
class Metrics:
    sent:      int = 0
    received:  int = 0
    latencies: list = field(default_factory=list)
    errors:    int = 0
    _lock:     threading.Lock = field(default_factory=threading.Lock)

    def inc_sent(self):
        with self._lock:
            self.sent += 1

    def inc_error(self):
        with self._lock:
            self.errors += 1

    def record(self, latency_ms: float):
        with self._lock:
            self.received += 1
            self.latencies.append(latency_ms)

    def snapshot(self) -> dict:
        with self._lock:
            lats = sorted(self.latencies)
            n = len(lats)
        return {
            "sent":     self.sent,
            "received": self.received,
            "errors":   self.errors,
            "loss_pct": round((1 - self.received / max(self.sent, 1)) * 100, 2),
            "latency": {
                "min":    round(min(lats),              2) if lats else None,
                "avg":    round(statistics.mean(lats),  2) if lats else None,
                "median": round(statistics.median(lats),2) if lats else None,
                "p95":    round(lats[int(n * 0.95)],   2) if n >= 20 else None,
                "p99":    round(lats[int(n * 0.99)],   2) if n >= 100 else None,
                "max":    round(max(lats),              2) if lats else None,
            },
        }


# Subscriber

def _build_payload(car_id: str, ts: float) -> str:
    return json.dumps({
        "car_id":          car_id,
        "latitude":        38.7223,
        "longitude":       -9.1393,
        "speed_kmh":       60.0,
        "heading_deg":     90.0,
        "speed_limit_kmh": 80,
        "emergency":       False,
        "timestamp":       ts,
    })


def _run_subscriber(host: str, port: int, metrics: Metrics, stop: threading.Event) -> None:
    client = mqtt.Client(client_id="perf-sub", protocol=mqtt.MQTTv311)

    def on_connect(c, _u, _f, rc):
        if rc == 0:
            c.subscribe(TOPIC, qos=0)

    def on_message(_c, _u, msg):
        try:
            payload = json.loads(msg.payload.decode())
            ts = payload.get("timestamp")
            if ts is not None:
                lat_ms = (time.time() - float(ts)) * 1000.0
                if 0 <= lat_ms < 60_000:
                    metrics.record(lat_ms)
        except Exception:
            pass

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, keepalive=10)
    client.loop_start()
    stop.wait()
    client.loop_stop()
    client.disconnect()


# Publisher

def _run_publisher(
    host: str, port: int, car_id: str,
    rate: float, metrics: Metrics, stop: threading.Event,
) -> None:
    client = mqtt.Client(client_id=f"perf-pub-{car_id}", protocol=mqtt.MQTTv311)
    client.connect(host, port, keepalive=10)
    client.loop_start()

    interval = 1.0 / rate if rate > 0 else 0.05
    while not stop.is_set():
        result = client.publish(TOPIC, _build_payload(car_id, time.time()), qos=0)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            metrics.inc_sent()
        else:
            metrics.inc_error()
        time.sleep(interval)

    client.loop_stop()
    client.disconnect()


# Spike scenario

def _run_spike(host: str, port: int, metrics: Metrics) -> None:
    phases = [
        (30, 10,  "normal"),
        (30, 200, "spike"),
        (60, 10,  "recovery"),
    ]
    stop = threading.Event()
    sub_t = threading.Thread(target=_run_subscriber, args=(host, port, metrics, stop), daemon=True)
    sub_t.start()

    for duration, rate, label in phases:
        print(f"  phase: {label} ({rate} msg/s for {duration} s)")
        pub_stops = [threading.Event() for _ in range(10)]
        threads = [
            threading.Thread(
                target=_run_publisher,
                args=(host, port, f"perf-car-{i}", rate / 10, metrics, s),
                daemon=True,
            )
            for i, s in enumerate(pub_stops)
        ]
        for t in threads:
            t.start()
        time.sleep(duration)
        for s in pub_stops:
            s.set()
        for t in threads:
            t.join(timeout=5)

    stop.set()
    sub_t.join(timeout=5)


# Reporting

def _print_report(snap: dict, elapsed: float) -> None:
    throughput = round(snap["received"] / max(elapsed, 1), 1)
    lat = snap["latency"]

    print()
    print("-" * 46)
    print(f"  Messages sent:     {snap['sent']}")
    print(f"  Messages received: {snap['received']}")
    print(f"  Message loss:      {snap['loss_pct']} %")
    print(f"  Errors (publish):  {snap['errors']}")
    print(f"  Throughput:        {throughput} msg/s")
    if lat["min"] is not None:
        print()
        print("  Broker latency (ms):")
        print(f"    min    {lat['min']}")
        print(f"    avg    {lat['avg']}")
        print(f"    median {lat['median']}")
        if lat["p95"] is not None:
            print(f"    p95    {lat['p95']}")
        if lat["p99"] is not None:
            print(f"    p99    {lat['p99']}")
        print(f"    max    {lat['max']}")
    print("-" * 46)
    print()

    _diagnose(snap, throughput)


def _diagnose(snap: dict, throughput: float) -> None:
    lat = snap["latency"]
    issues = []

    if snap["loss_pct"] > 1.0:
        issues.append(f"message loss {snap['loss_pct']}% > 1% - broker queue overflowing or subscriber too slow")
    if lat["p95"] is not None and lat["p95"] > 100:
        issues.append(f"p95 latency {lat['p95']} ms - broker may be CPU-bound or persistence enabled (mosquitto.conf)")
    if throughput < 10:
        issues.append("throughput < 10 msg/s - check broker is reachable and subscriber connected")

    if issues:
        print("  Bottlenecks:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  No bottlenecks detected at this load level.")
    print()


# CLI

SCENARIOS = {
    "baseline": dict(publishers=1,  rate=10,  duration=30),
    "load":     dict(publishers=10, rate=50,  duration=60),
    "stress":   dict(publishers=50, rate=200, duration=60),
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MQTT load test for pei-automotive-backend")
    p.add_argument("--host",       default="localhost")
    p.add_argument("--port",       type=int,   default=1884)
    p.add_argument("--publishers", type=int,   default=10)
    p.add_argument("--rate",       type=float, default=50)
    p.add_argument("--duration",   type=int,   default=60)
    p.add_argument("--scenario",   choices=["baseline", "load", "stress", "spike"], default=None)
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.scenario and args.scenario != "spike":
        cfg = SCENARIOS[args.scenario]
        publishers, rate, duration = cfg["publishers"], cfg["rate"], cfg["duration"]
    elif args.scenario != "spike":
        publishers, rate, duration = args.publishers, args.rate, args.duration
    else:
        publishers = rate = duration = None

    metrics = Metrics()

    print()
    print("  pei-automotive / MQTT load test")
    print(f"  broker: {args.host}:{args.port}")

    if args.scenario == "spike":
        print("  scenario: spike (10 -> 200 -> 10 msg/s)")
        print()
        t0 = time.time()
        _run_spike(args.host, args.port, metrics)
        elapsed = time.time() - t0
    else:
        rate_per_pub = rate / max(publishers, 1)
        print(f"  publishers: {publishers}, rate: {rate} msg/s total, duration: {duration} s")
        print()

        stop  = threading.Event()
        sub_t = threading.Thread(target=_run_subscriber, args=(args.host, args.port, metrics, stop), daemon=True)
        pub_ts = [
            threading.Thread(
                target=_run_publisher,
                args=(args.host, args.port, f"perf-car-{i}", rate_per_pub, metrics, stop),
                daemon=True,
            )
            for i in range(publishers)
        ]

        sub_t.start()
        time.sleep(0.5)
        t0 = time.time()
        for t in pub_ts:
            t.start()

        time.sleep(duration)
        stop.set()

        for t in pub_ts:
            t.join(timeout=5)
        time.sleep(0.3)
        sub_t.join(timeout=5)
        elapsed = time.time() - t0

    _print_report(metrics.snapshot(), elapsed)


if __name__ == "__main__":
    main()
