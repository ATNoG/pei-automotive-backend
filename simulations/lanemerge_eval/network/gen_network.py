#!/usr/bin/env python3
"""
Generate simulations/lanemerge_eval/network/lanemerge.net.xml from the
GPS coordinates of the lane-merge scenario without requiring netconvert.

The network has three edges:
  highway_in  : H_START  → MERGE  (main lane approaching)
  highway_out : MERGE    → H_END  (main lane continuing)
  entering    : E_START  → MERGE  (merging ramp)

GPS geometry is derived from simulations/roads/highway.json and entering.json
so that TraCI's convertGeo() returns coordinates that fall on those routes
and the LaneMergeDetector classifies cars correctly.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

# GPS anchor points (from highway.json / entering.json)
GPS = {
    "H_START": (40.627244, -8.730434), # highway start
    "MERGE":   (40.628003, -8.732348), # merge / junction
    "H_END":   (40.628800, -8.734500), # highway continues
    "E_START": (40.628041, -8.731600), # entering ramp start
}

# UTM zone 29N conversion (WGS84)
_A   = 6_378_137.0
_F   = 1 / 298.257_223_563
_B   = _A * (1 - _F)
_E2  = 1 - (_B / _A) ** 2
_K0  = 0.9996
_E0  = 500_000.0 # false easting
_ZONE_CM = -9.0 # central meridian zone 29 (°)


def _utm29(lat: float, lon: float) -> tuple[float, float]:
    """Return (easting, northing) in UTM zone 29N for a WGS84 point."""
    lat_r = math.radians(lat)
    dlon_r = math.radians(lon - _ZONE_CM)

    sin_lat = math.sin(lat_r)
    cos_lat = math.cos(lat_r)
    tan_lat = math.tan(lat_r)

    N = _A / math.sqrt(1 - _E2 * sin_lat ** 2)
    T = tan_lat ** 2
    C = _E2 / (1 - _E2) * cos_lat ** 2
    A_val = cos_lat * dlon_r

    # Meridional arc
    e2 = _E2
    M = _A * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_r
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat_r)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat_r)
        - (35 * e2**3 / 3072) * math.sin(6 * lat_r)
    )

    easting = _K0 * N * (
        A_val
        + (1 - T + C) * A_val**3 / 6
        + (5 - 18 * T + T**2 + 72 * C - 58 * _E2 / (1 - _E2)) * A_val**5 / 120
    ) + _E0

    northing = _K0 * (
        M
        + N * tan_lat * (
            A_val**2 / 2
            + (5 - T + 9 * C + 4 * C**2) * A_val**4 / 24
            + (61 - 58 * T + T**2 + 600 * C - 330 * _E2 / (1 - _E2)) * A_val**6 / 720
        )
    )
    return easting, northing


def _dist(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def _shape_str(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.4f},{y:.4f}" for x, y in points)

# Build network
def generate(out_path: Path) -> None:
    # Convert all GPS points to UTM
    utm = {k: _utm29(lat, lon) for k, (lat, lon) in GPS.items()}

    # Cartesian coords relative to MERGE (so MERGE lives at (0, 0))
    ox, oy = utm["MERGE"]
    xy = {k: (ex - ox, ey - oy) for k, (ex, ey) in utm.items()}

    merge  = xy["MERGE"]    # (0, 0)
    h_start = xy["H_START"]
    h_end  = xy["H_END"]
    e_start = xy["E_START"]

    # Lane width and internal edge stub length
    lane_width = 3.20
    stub = 2.0   # small internal edge length at the junction

    # Heading vectors for edges arriving / leaving at MERGE
    def unit_vec(src, dst):
        dx, dy = dst[0] - src[0], dst[1] - src[1]
        l = math.hypot(dx, dy)
        return dx / l, dy / l

    # highway_in arrives from H_START → MERGE
    uhi = unit_vec(h_start, merge)   # unit vector arriving at MERGE
    # entering arrives from E_START → MERGE
    uei = unit_vec(e_start, merge)
    # highway_out departs MERGE → H_END
    uho = unit_vec(merge, h_end)     # unit vector leaving MERGE

    # Internal edge start/end points (short stubs at the junction)
    # :MERGE_0  highway_in → highway_out
    m0_start = (merge[0] - uhi[0] * stub, merge[1] - uhi[1] * stub)
    m0_end   = (merge[0] + uho[0] * stub, merge[1] + uho[1] * stub)
    # :MERGE_1  entering → highway_out
    m1_start = (merge[0] - uei[0] * stub, merge[1] - uei[1] * stub)
    m1_end   = (merge[0] + uho[0] * stub, merge[1] + uho[1] * stub)

    # Build XML
    root = ET.Element("net", {
        "version": "1.16",
        "junctionCornerDetail": "5",
        "limitTurnSpeed": "5.50",
    })

    # Location (netOffset maps the UTM origin back to (0,0))
    ET.SubElement(root, "location", {
        "netOffset": f"{-ox:.4f},{-oy:.4f}",
        "origBoundary": "-8.7360,40.6260,-8.7290,40.6300",
        "convBoundary": f"{min(h_start[0], h_end[0], e_start[0]):.2f},"
                        f"{min(h_start[1], h_end[1], e_start[1]):.2f},"
                        f"{max(h_start[0], h_end[0], e_start[0]):.2f},"
                        f"{max(h_start[1], h_end[1], e_start[1]):.2f}",
        "projParameter": "+proj=utm +zone=29 +ellps=WGS84 +datum=WGS84 +units=m +no_defs",
    })

    # Edge types
    ET.SubElement(root, "type", {
        "id": "road", "priority": "9", "numLanes": "1",
        "speed": "27.78", "disallow": "",
    })

    # Dead-end junctions
    for node_id, pos in [("H_START", h_start), ("H_END", h_end), ("E_START", e_start)]:
        ET.SubElement(root, "junction", {
            "id": node_id, "type": "dead_end",
            "x": f"{pos[0]:.4f}", "y": f"{pos[1]:.4f}",
            "incLanes": "", "intLanes": "", "shape": "",
        })

    # Merge junction
    # Junction shape: small hexagon around (0,0)
    r = 5.0
    angles = [0, 60, 120, 180, 240, 300]
    jshape = " ".join(
        f"{r*math.cos(math.radians(a)):.4f},{r*math.sin(math.radians(a)):.4f}"
        for a in angles
    )
    ET.SubElement(root, "junction", {
        "id": "MERGE", "type": "zipper",
        "x": "0.0000", "y": "0.0000",
        "incLanes": "highway_in_0 entering_0",
        "intLanes": ":MERGE_0_0 :MERGE_1_0",
        "shape": jshape,
    })
    # foeDetector children
    junc_el = root.find("junction[@id='MERGE']")
    req0 = ET.SubElement(junc_el, "request", {
        "index": "0", "response": "10", "foes": "10", "cont": "0",
    })
    req1 = ET.SubElement(junc_el, "request", {
        "index": "1", "response": "01", "foes": "01", "cont": "0",
    })

    # Regular edges
    def add_edge(eid, from_node, to_node, shape_pts, speed="27.78", priority="9"):
        length = sum(_dist(shape_pts[i], shape_pts[i+1]) for i in range(len(shape_pts)-1))
        e = ET.SubElement(root, "edge", {
            "id": eid, "from": from_node, "to": to_node,
            "priority": priority, "type": "road",
        })
        ET.SubElement(e, "lane", {
            "id": f"{eid}_0", "index": "0",
            "speed": speed, "length": f"{length:.4f}",
            "width": f"{lane_width:.2f}",
            "shape": _shape_str(shape_pts),
        })

    add_edge("highway_in",  "H_START", "MERGE",  [h_start, merge])
    add_edge("highway_out", "MERGE",   "H_END",   [merge, h_end])
    add_edge("entering",    "E_START", "MERGE",  [e_start, merge], speed="22.22", priority="7")

    # Internal edges
    def add_internal(eid, shape_pts):
        length = max(_dist(shape_pts[0], shape_pts[-1]), 0.01)
        e = ET.SubElement(root, "edge", {
            "id": eid, "function": "internal",
        })
        ET.SubElement(e, "lane", {
            "id": f"{eid}_0", "index": "0",
            "speed": "8.33", "length": f"{length:.4f}",
            "shape": _shape_str(shape_pts),
        })

    add_internal(":MERGE_0", [m0_start, m0_end])
    add_internal(":MERGE_1", [m1_start, m1_end])

    # Connections
    def add_conn(from_e, to_e, via, link_idx, state="M"):
        ET.SubElement(root, "connection", {
            "from": from_e, "to": to_e,
            "fromLane": "0", "toLane": "0",
            "via": via, "tl": "", "linkIndex": str(link_idx),
            "dir": "s", "state": state,
        })

    add_conn("highway_in", "highway_out", ":MERGE_0_0", 0)
    add_conn("entering",   "highway_out", ":MERGE_1_0", 1)
    # internal → outgoing connections
    ET.SubElement(root, "connection", {
        "from": ":MERGE_0", "to": "highway_out",
        "fromLane": "0", "toLane": "0", "dir": "s", "state": "M",
    })
    ET.SubElement(root, "connection", {
        "from": ":MERGE_1", "to": "highway_out",
        "fromLane": "0", "toLane": "0", "dir": "s", "state": "M",
    })

    # Write
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(str(out_path), encoding="unicode", xml_declaration=True)
    print(f"Written: {out_path}")


if __name__ == "__main__":
    here = Path(__file__).parent
    generate(here / "lanemerge.net.xml")
