#!/usr/bin/env python3
"""
Pipeline load test - N simulated cars injecting GPS via Ditto REST concurrently.

No Hono device registration required.  Car IDs are generated automatically
(org.acme:load-car-000 …).  Ditto things are created in a setup step before
the test begins.

Measures four per-injection latencies:
  client_ditto_ms  - HTTP PUT to Ditto (client → Ditto round-trip)
  ditto_proc_ms    - PUT returns → processor publishes  (Ditto WS propagation)
  proc_client_ms   - processor timestamp → subscriber receives  (Overpass + MQTT)
  e2e_ms           - full pipeline (client → subscriber)

Generates three plots in tests/performance/plots/:
  latency_stages.png      - bar chart: avg ± std per stage
  latency_boxplot.png     - box plots per stage
  latency_timeseries.png  - e2e scatter over time

Usage:
    python3 tests/performance/measure_latency.py --cars 20 --duration 60
    python3 tests/performance/measure_latency.py --cars 100 --duration 120 --rate 0.5
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore", category=DeprecationWarning)

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

load_dotenv()

DITTO_API  = os.getenv("DITTO_API_URL", "").rstrip("/")
DITTO_AUTH = (os.getenv("DITTO_USER", "ditto"), os.getenv("DITTO_PASS", "ditto"))
MQTT_HOST  = "localhost"
MQTT_PORT  = 1884

PLOTS_DIR = Path(__file__).parent / "plots"

_BASE_LAT = 40.6405
_BASE_LON = -8.6538
_DELTA    = 0.00001

_NAMESPACE = "org.acme"
_PREFIX    = "load-car"


def _car_id(n: int) -> str:
    return f"{_PREFIX}-{n:03d}"


def _thing_id(n: int) -> str:
    return f"{_NAMESPACE}:{_car_id(n)}"


# ── Ditto setup ────────────────────────────────────────────────────────────────

def _create_things(n: int, session: requests.Session) -> None:
    """Create N Ditto things in parallel (idempotent - safe to re-run)."""
    errors: list[str] = []
    lock = threading.Lock()

    def _create(i: int) -> None:
        tid = _thing_id(i)
        r = session.put(
            f"{DITTO_API}/api/2/things/{tid}",
            json={"attributes": {"created_by": "measure_latency"}},
            timeout=15,
        )
        if r.status_code not in (200, 201, 204):
            with lock:
                errors.append(f"{tid}: {r.status_code}")

    threads = [threading.Thread(target=_create, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    if errors:
        sys.exit(f"Failed to create Ditto things:\n" + "\n".join(errors))


# ── E2E subscriber ─────────────────────────────────────────────────────────────

class _E2ESubscriber:
    """
    Subscribes to cars/updates.  Queues (t_received, proc_ts) per car_id.
    next_after(car_id, t0) returns the first arrival where t_received > t0.
    """

    def __init__(self, host: str, port: int, car_ids: set[str]) -> None:
        self._qs: dict[str, queue.Queue] = {cid: queue.Queue() for cid in car_ids}
        client = mqtt.Client(protocol=mqtt.MQTTv311)
        client.on_connect = (
            lambda c, _u, _f, rc: c.subscribe("cars/updates", qos=0) if rc == 0 else None
        )
        client.on_message = self._on_message
        client.connect(host, port, keepalive=10)
        client.loop_start()
        self._client = client

    def _on_message(self, _c, _u, msg) -> None:
        t_recv = time.time()
        try:
            payload = json.loads(msg.payload.decode())
            cid = payload.get("car_id")
            if cid and cid in self._qs:
                self._qs[cid].put((t_recv, payload.get("timestamp")))
        except Exception:
            pass

    def next_after(
        self, car_id: str, t0: float, timeout: float = 30.0
    ) -> Optional[tuple[float, Optional[float]]]:
        q = self._qs[car_id]
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                t_recv, proc_ts = q.get(timeout=min(remaining, 0.5))
                if t_recv > t0:
                    return t_recv, proc_ts
                # stale (warmup / previous round) - discard
            except queue.Empty:
                if time.time() >= deadline:
                    return None

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


# ── injection worker ───────────────────────────────────────────────────────────

def _worker(
    car_n: int,
    rate: float,
    stop: threading.Event,
    sub: _E2ESubscriber,
    records: list,
    lock: threading.Lock,
    session: requests.Session,
) -> None:
    tid    = _thing_id(car_n)
    cid    = _car_id(car_n)
    seq    = 0
    period = 1.0 / max(rate, 0.001)

    while not stop.is_set():
        lat = _BASE_LAT + (seq % 500) * _DELTA
        lon = _BASE_LON + (seq % 500) * _DELTA
        body = {
            "gps":  {"properties": {"latitude": lat, "longitude": lon}},
            "info": {"properties": {"emergency": False}},
        }
        t0 = time.time()
        try:
            r = session.put(
                f"{DITTO_API}/api/2/things/{tid}/features",
                json=body,
                timeout=15,
            )
            if r.status_code not in (200, 201, 204):
                time.sleep(period)
                continue
        except Exception:
            time.sleep(period)
            continue

        t_put = time.time()
        result = sub.next_after(cid, t0, timeout=30.0)
        t_recv = proc_ts = None
        if result:
            t_recv, proc_ts = result

        rec = {
            "car":            cid,
            "t_send":         t0,
            "client_ditto_ms": round((t_put  - t0)    * 1000, 1),
            "ditto_proc_ms":   round((proc_ts - t_put) * 1000, 1) if proc_ts else None,
            "proc_client_ms":  round((t_recv  - proc_ts) * 1000, 1) if (t_recv and proc_ts) else None,
            "e2e_ms":          round((t_recv  - t0)    * 1000, 1) if t_recv else None,
        }
        with lock:
            records.append(rec)

        elapsed = time.time() - t0
        sleep_for = max(0.0, period - elapsed)
        if sleep_for > 0:
            stop.wait(timeout=sleep_for)
        seq += 1


# ── stats helpers ──────────────────────────────────────────────────────────────

def _pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return s[min(int(len(s) * p), len(s) - 1)]


def _agg(records: list[dict]) -> dict[str, dict]:
    buckets: dict[str, list[float]] = {
        "client_ditto_ms": [],
        "ditto_proc_ms":   [],
        "proc_client_ms":  [],
        "e2e_ms":          [],
    }
    for r in records:
        for k in buckets:
            v = r.get(k)
            if v is not None:
                buckets[k].append(v)
    out = {}
    for k, vals in buckets.items():
        if not vals:
            out[k] = {}
            continue
        import statistics
        out[k] = {
            "n":   len(vals),
            "avg": round(sum(vals) / len(vals), 1),
            "std": round(statistics.stdev(vals), 1) if len(vals) > 1 else 0.0,
            "p50": round(_pct(vals, 0.50), 1),
            "p95": round(_pct(vals, 0.95), 1),
            "max": round(max(vals), 1),
        }
    return out


# ── plotting ───────────────────────────────────────────────────────────────────

_STAGE_LABELS = {
    "client_ditto_ms": "Client→Ditto\n(HTTP PUT)",
    "ditto_proc_ms":   "Ditto→Processor\n(WS propagation)",
    "proc_client_ms":  "Processor→Client\n(Overpass + MQTT)",
    "e2e_ms":          "E2E\n(total)",
}

_STAGE_COLORS = {
    "client_ditto_ms": "#4e79a7",
    "ditto_proc_ms":   "#f28e2b",
    "proc_client_ms":  "#59a14f",
    "e2e_ms":          "#e15759",
}


def _plot(records: list[dict], agg: dict, out_dir: Path, n_cars: int) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        import numpy as np
    except ImportError:
        print("  matplotlib not installed - skipping plots  (pip install matplotlib)")
        return

    plt.rcParams.update({
        "font.family":       "sans-serif",
        "axes.spines.top":   False,
        "axes.spines.right": False,
        "axes.grid":         True,
        "grid.color":        "#e0e0e0",
        "grid.linewidth":    0.8,
        "figure.dpi":        150,
        "axes.labelsize":    10,
        "axes.titlesize":    11,
    })

    out_dir.mkdir(parents=True, exist_ok=True)
    stages   = list(_STAGE_LABELS.keys())
    labels   = [_STAGE_LABELS[s] for s in stages]
    colors   = [_STAGE_COLORS[s] for s in stages]
    avgs     = [agg[s].get("avg", 0) or 0 for s in stages]
    stds     = [agg[s].get("std", 0) or 0 for s in stages]
    p95s     = [agg[s].get("p95", 0) or 0 for s in stages]

    # ── 1. Bar chart: avg ± std and p95 ──────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle(
        f"Pipeline Stage Latency  -  {n_cars} simulated cars",
        fontsize=13, fontweight="bold",
    )
    x      = np.arange(len(stages))
    width  = 0.38

    bars_avg = ax.bar(x - width / 2, avgs, width, color=colors, alpha=0.85,
                      label="avg", zorder=3)
    bars_p95 = ax.bar(x + width / 2, p95s, width, color=colors, alpha=0.45,
                      label="p95", zorder=3, hatch="///", edgecolor="white")

    # error bars on avg
    ax.errorbar(x - width / 2, avgs, yerr=stds,
                fmt="none", color="#333333", capsize=5, linewidth=1.5, zorder=4)

    # value labels on bars
    for bar in bars_avg:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, h + max(avgs) * 0.015,
                    f"{h:.0f}", ha="center", va="bottom", fontsize=8, fontweight="bold")
    for bar in bars_p95:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, h + max(p95s) * 0.015,
                    f"{h:.0f}", ha="center", va="bottom", fontsize=8, color="#555555")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Latency (ms)")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%g ms"))
    ax.legend(loc="upper left", fontsize=9, framealpha=0.7)
    ax.set_ylim(0, max(max(avgs + p95s), 1) * 1.25)

    # sample count subtitle
    n_e2e = agg["e2e_ms"].get("n", 0)
    ax.set_title(f"n = {n_e2e} complete measurements  |  error bars = ±1 std",
                 fontsize=9, color="#666666", pad=4)

    fig.tight_layout()
    dest = out_dir / "latency_stages.png"
    fig.savefig(dest, bbox_inches="tight")
    plt.close(fig)
    print(f"    → {dest.name}")

    # ── 2. Box plots ──────────────────────────────────────────────────────────
    data = []
    box_labels = []
    box_colors = []
    for s in stages:
        vals = [r[s] for r in records if r.get(s) is not None]
        if vals:
            data.append(vals)
            box_labels.append(_STAGE_LABELS[s])
            box_colors.append(_STAGE_COLORS[s])

    if data:
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.suptitle(
            f"Pipeline Stage Latency Distribution  -  {n_cars} simulated cars",
            fontsize=13, fontweight="bold",
        )
        bp = ax.boxplot(
            data,
            patch_artist=True,
            medianprops=dict(color="#111111", linewidth=2),
            whiskerprops=dict(linewidth=1.2),
            capprops=dict(linewidth=1.2),
            flierprops=dict(marker="o", markersize=3, alpha=0.35, linestyle="none"),
            widths=0.55,
        )
        for patch, col in zip(bp["boxes"], box_colors):
            r, g, b, _ = matplotlib.colors.to_rgba(col)
            patch.set_facecolor((r, g, b, 0.65))
            patch.set_edgecolor(col)

        ax.set_xticks(range(1, len(box_labels) + 1))
        ax.set_xticklabels(box_labels, fontsize=9)
        ax.set_ylabel("Latency (ms)")
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%g ms"))
        fig.tight_layout()
        dest = out_dir / "latency_boxplot.png"
        fig.savefig(dest, bbox_inches="tight")
        plt.close(fig)
        print(f"    → {dest.name}")

    # ── 3. Time series: e2e over time ─────────────────────────────────────────
    e2e_recs = [(r["t_send"], r["e2e_ms"]) for r in records if r.get("e2e_ms") is not None]
    if e2e_recs:
        t_origin = min(t for t, _ in e2e_recs)
        xs = [t - t_origin for t, _ in e2e_recs]
        ys = [ms for _, ms in e2e_recs]

        fig, ax = plt.subplots(figsize=(13, 4))
        fig.suptitle(
            f"E2E Latency over Time  -  {n_cars} simulated cars",
            fontsize=13, fontweight="bold",
        )
        ax.scatter(xs, ys, s=10, alpha=0.45, color=_STAGE_COLORS["e2e_ms"], zorder=3)

        # rolling average
        if len(ys) > 20:
            w = max(5, len(ys) // 30)
            smooth_y = [sum(ys[max(0, j - w): j + 1]) / len(ys[max(0, j - w): j + 1])
                        for j in range(len(ys))]
            ax.plot(xs, smooth_y, color=_STAGE_COLORS["e2e_ms"], linewidth=2, alpha=0.7,
                    label=f"rolling avg (w={w})")
            ax.legend(fontsize=8)

        ax.set_xlabel("Elapsed (s)")
        ax.set_ylabel("Latency (ms)")
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%g ms"))
        ax.axhline(
            agg["e2e_ms"].get("avg", 0),
            color="#333333", linestyle="--", linewidth=1, alpha=0.5,
            label=f"avg {agg['e2e_ms'].get('avg', 0):.0f} ms",
        )
        fig.tight_layout()
        dest = out_dir / "latency_timeseries.png"
        fig.savefig(dest, bbox_inches="tight")
        plt.close(fig)
        print(f"    → {dest.name}")


# ── console summary ────────────────────────────────────────────────────────────

def _print_summary(agg: dict) -> None:
    W = 70
    print()
    print("-" * W)
    print(f"  {'Stage':<32} {'avg':>8} {'p50':>8} {'p95':>8} {'max':>8}  n")
    print("-" * W)
    for key, label in _STAGE_LABELS.items():
        s = agg.get(key, {})
        if not s:
            print(f"  {label.replace(chr(10), ' '):<32}  {'n/a':>8}")
            continue
        print(
            f"  {label.replace(chr(10), ' '):<32}"
            f"  {s['avg']:>6.0f} ms"
            f"  {s['p50']:>6.0f} ms"
            f"  {s['p95']:>6.0f} ms"
            f"  {s['max']:>6.0f} ms"
            f"  {s['n']}"
        )
    print("-" * W)
    print()


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pipeline load test - N simulated cars via Ditto REST"
    )
    p.add_argument("--cars",     type=int,   default=20,   help="Simulated cars (default: 20)")
    p.add_argument("--duration", type=int,   default=60,   help="Test duration in seconds (default: 60)")
    p.add_argument("--rate",     type=float, default=1.0,  help="Injections/sec per car (default: 1.0)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if not DITTO_API:
        sys.exit("DITTO_API_URL not set - add it to .env before running")

    n    = args.cars
    rate = args.rate

    print()
    print("  pei-automotive / pipeline load test")
    print(f"  cars: {n}  duration: {args.duration} s  rate: {rate} req/s per car")
    print(f"  ditto: {DITTO_API}")
    print()

    session = requests.Session()
    session.auth = DITTO_AUTH

    # create Ditto things (idempotent)
    print(f"  [1/4] creating {n} Ditto things ...")
    _create_things(n, session)
    print("        done\n")

    # subscriber
    car_ids = {_car_id(i) for i in range(n)}
    sub = _E2ESubscriber(MQTT_HOST, MQTT_PORT, car_ids)
    time.sleep(0.5)

    # warmup - prime position_processor state and Overpass cache
    print("  [2/4] warming up (5 rounds, all cars) ...")
    warmup_session = requests.Session()
    warmup_session.auth = DITTO_AUTH
    for w in range(5):
        lat = _BASE_LAT - (w + 1) * _DELTA
        lon = _BASE_LON - (w + 1) * _DELTA
        wthreads = []
        for i in range(n):
            tid = _thing_id(i)
            cid = _car_id(i)
            def _warm(tid=tid, cid=cid):
                try:
                    warmup_session.put(
                        f"{DITTO_API}/api/2/things/{tid}/features",
                        json={"gps": {"properties": {"latitude": lat, "longitude": lon}},
                              "info": {"properties": {"emergency": False}}},
                        timeout=15,
                    )
                    sub.next_after(cid, time.time() - 5, timeout=10.0)
                except Exception:
                    pass
            t = threading.Thread(target=_warm, daemon=True)
            t.start()
            wthreads.append(t)
        for t in wthreads:
            t.join(timeout=15)
        time.sleep(0.5)
    time.sleep(2.0)
    print("        done\n")

    # run load test
    print(f"  [3/4] running ({args.duration} s, {n} cars at {rate} req/s each) ...")
    records: list[dict] = []
    lock    = threading.Lock()
    stop    = threading.Event()

    sessions = [requests.Session() for _ in range(n)]
    for s in sessions:
        s.auth = DITTO_AUTH

    workers = [
        threading.Thread(
            target=_worker,
            args=(i, rate, stop, sub, records, lock, sessions[i]),
            daemon=True,
        )
        for i in range(n)
    ]
    for w in workers:
        w.start()

    t_start = time.time()
    while True:
        elapsed = time.time() - t_start
        if elapsed >= args.duration:
            break
        with lock:
            n_rec = len(records)
        print(f"    {elapsed:>5.0f}s  {n_rec} measurements", end="\r", flush=True)
        time.sleep(2.0)

    stop.set()
    for w in workers:
        w.join(timeout=35)
    sub.stop()
    print(f"\n        collected {len(records)} measurements\n")

    # results
    print("  [4/4] results")
    agg = _agg(records)
    _print_summary(agg)

    # save JSON
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    import datetime
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_out = PLOTS_DIR / f"latency_{ts}.json"
    json_out.write_text(json.dumps(records, indent=2))
    print(f"  raw data → {json_out}")

    # plots
    print("  generating plots ...")
    _plot(records, agg, PLOTS_DIR, n)
    print(f"\n  done - plots in {PLOTS_DIR}\n")


if __name__ == "__main__":
    main()
