#!/usr/bin/env python3
"""Pipeline latency measurement.

Subscribes to cars/updates/+ while running the bridge and splits latency
into two distinct hops:

  queue latency   : pf_ts   → pp_rx_ts   (message sitting in MQTT queue)
  process latency : pp_rx_ts → timestamp  (Overpass lookup + computation)

Usage:
    python tests/performance/measure_pipeline_latency.py \\
        --cfg simulations/SUMO/osm.sumocfg \\
        --real-time --max-steps 60 --drain 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import paho.mqtt.client as mqtt

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import bridge  # noqa: E402

_DEFAULT_OUTPUT = _REPO_ROOT / "tests" / "performance" / "results"
_MQTT_HOST = "localhost"
_MQTT_PORT = 1884


def _collect(
    cfg: Path,
    route_files: list[Path] | None,
    step_length: float,
    end_time: float | None,
    max_steps: int | None,
    workers: int,
    real_time: bool,
    drain: float,
    mqtt_host: str,
    mqtt_port: int,
) -> dict[str, dict[str, list[float]]]:
    # per car: {"queue": [...ms], "process": [...ms]}
    data_by_car: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"queue": [], "process": []}
    )

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
        except Exception:
            return
        pf_ts    = data.get("pf_ts")
        pp_rx_ts = data.get("pp_rx_ts")
        pp_tx_ts = data.get("timestamp")
        car_id   = data.get("car_id")
        if None in (pf_ts, pp_rx_ts, pp_tx_ts, car_id):
            return
        data_by_car[car_id]["queue"].append((pp_rx_ts - pf_ts) * 1000.0)
        data_by_car[car_id]["process"].append((pp_tx_ts - pp_rx_ts) * 1000.0)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(mqtt_host, mqtt_port)
    client.subscribe("cars/updates/+", qos=1)
    client.loop_start()
    time.sleep(0.3)

    bridge.run(
        cfg=cfg,
        route_files=route_files,
        step_length=step_length,
        end_time=end_time,
        max_steps=max_steps,
        workers=workers,
        real_time=real_time,
        cleanup=True,
        post_sim_drain=drain,
    )

    time.sleep(2.0)
    client.loop_stop()
    client.disconnect()
    return dict(data_by_car)


def _print_summary(data_by_car: dict[str, dict[str, list[float]]]) -> None:
    for label, key in [("queue latency (pf_ts → pp received)", "queue"),
                       ("process latency (pp received → cars/updates)", "process")]:
        all_s = [v for d in data_by_car.values() for v in d[key]]
        header = f"{'car':<30} {'n':>5} {'mean':>9} {'p50':>9} {'p95':>9} {'max':>9}"
        print(f"\n{label}")
        print("-" * len(header))
        print(header)
        print("-" * len(header))
        for car in sorted(data_by_car):
            s = data_by_car[car][key]
            print(
                f"{car:<30} {len(s):>5}"
                f" {np.mean(s):>8.1f}ms"
                f" {np.percentile(s, 50):>8.1f}ms"
                f" {np.percentile(s, 95):>8.1f}ms"
                f" {max(s):>8.1f}ms"
            )
        print("-" * len(header))
        print(
            f"{'total':<30} {len(all_s):>5}"
            f" {np.mean(all_s):>8.1f}ms"
            f" {np.percentile(all_s, 50):>8.1f}ms"
            f" {np.percentile(all_s, 95):>8.1f}ms"
            f" {max(all_s):>8.1f}ms\n"
        )


def _plot(data_by_car: dict[str, dict[str, list[float]]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cars = sorted(data_by_car)

    # per-car subplots: queue vs process latency over message index
    fig, axes = plt.subplots(len(cars), 1, figsize=(12, 3 * len(cars)), sharex=False)
    if len(cars) == 1:
        axes = [axes]
    for ax, car in zip(axes, cars):
        q = data_by_car[car]["queue"]
        p = data_by_car[car]["process"]
        ax.plot(q, linewidth=0.8, label="queue wait")
        ax.plot(p, linewidth=0.8, label="processing")
        ax.axhline(np.mean(q), color="steelblue", linestyle="--", linewidth=1,
                   label=f"queue avg {np.mean(q):.0f}ms")
        ax.axhline(np.mean(p), color="darkorange", linestyle="--", linewidth=1,
                   label=f"process avg {np.mean(p):.0f}ms")
        ax.set_title(car, fontsize=9)
        ax.set_ylabel("ms")
        ax.set_xlabel("message index")
        ax.legend(fontsize=7, ncol=2)
    fig.suptitle("queue wait vs processing time per car", fontsize=11)
    fig.tight_layout()
    path = output_dir / "latency_per_car.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"saved {path}")

    # stacked bar: avg queue + avg process per car
    q_avgs = [np.mean(data_by_car[c]["queue"]) for c in cars]
    p_avgs = [np.mean(data_by_car[c]["process"]) for c in cars]
    fig, ax = plt.subplots(figsize=(max(6, len(cars) * 0.9), 4))
    ax.bar(cars, q_avgs, label="queue wait")
    ax.bar(cars, p_avgs, bottom=q_avgs, label="processing")
    ax.set_ylabel("avg latency (ms)")
    ax.set_title("per-car average latency breakdown")
    ax.tick_params(axis="x", rotation=45)
    ax.legend()
    fig.tight_layout()
    path = output_dir / "latency_avg_per_car.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"saved {path}")

    # histogram: queue and process side by side
    all_q = [v for d in data_by_car.values() for v in d["queue"]]
    all_p = [v for d in data_by_car.values() for v in d["process"]]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    ax1.hist(all_q, bins=40, edgecolor="black", linewidth=0.4)
    ax1.axvline(np.mean(all_q), color="red", linestyle="--",
                label=f"mean {np.mean(all_q):.0f}ms")
    ax1.axvline(np.percentile(all_q, 95), color="orange", linestyle="--",
                label=f"p95 {np.percentile(all_q, 95):.0f}ms")
    ax1.set_title("queue wait distribution")
    ax1.set_xlabel("ms")
    ax1.legend()

    ax2.hist(all_p, bins=40, edgecolor="black", linewidth=0.4, color="darkorange")
    ax2.axvline(np.mean(all_p), color="red", linestyle="--",
                label=f"mean {np.mean(all_p):.0f}ms")
    ax2.axvline(np.percentile(all_p, 95), color="steelblue", linestyle="--",
                label=f"p95 {np.percentile(all_p, 95):.0f}ms")
    ax2.set_title("processing time distribution")
    ax2.set_xlabel("ms")
    ax2.legend()

    fig.suptitle("overall latency distributions", fontsize=11)
    fig.tight_layout()
    path = output_dir / "latency_histogram.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"saved {path}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="measure pipeline latency: queue wait and processing time per car"
    )
    p.add_argument("--cfg", required=True, type=Path,
                   help="path to SUMO .sumocfg file")
    p.add_argument("--route-files", nargs="+", type=Path, default=None)
    p.add_argument("--step-length", type=float, default=1.0)
    p.add_argument("--end-time", type=float, default=None)
    p.add_argument("--max-steps", type=int, default=None,
                   help="stop after this many simulation steps")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--real-time", action="store_true")
    p.add_argument("--drain", type=float, default=5.0,
                   help="seconds to wait after sim ends for trailing messages (default: 5)")
    p.add_argument("--mqtt-host", default=_MQTT_HOST)
    p.add_argument("--mqtt-port", type=int, default=_MQTT_PORT)
    p.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT)
    args = p.parse_args()

    data_by_car = _collect(
        cfg=args.cfg,
        route_files=args.route_files,
        step_length=args.step_length,
        end_time=args.end_time,
        max_steps=args.max_steps,
        workers=args.workers,
        real_time=args.real_time,
        drain=args.drain,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
    )

    if not data_by_car:
        print("no data collected — check that pf_ts/pp_rx_ts are injected by the services")
        return 1

    _print_summary(data_by_car)
    _plot(data_by_car, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
