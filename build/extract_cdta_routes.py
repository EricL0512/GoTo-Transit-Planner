#!/usr/bin/env python3
"""
Replace the hand-drawn cdtaRoutes paths and transitStops in index.html with
real CDTA GTFS shapes and timepoint stops.

For each route, picks the shape used by the most trips (the main pattern,
ignoring short-turns / deviations), simplifies it with Douglas-Peucker so the
HTML stays small, and rewrites only that route's `path:[...]` array.
Safe to re-run.

Usage: python build/extract_cdta_routes.py [path/to/gtfs.zip] [path/to/index.html]
Defaults: data/gtfs/cdta_gtfs.zip and app/index.html
GTFS source: https://www.cdta.org/schedules/google_transit.zip
"""
import csv, io, json, math, os, re, sys, zipfile
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GTFS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "gtfs", "cdta_gtfs.zip"))
HTML = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "app", "index.html"))

# index.html route id -> GTFS route_short_name
ROUTES = {"rt85": "85", "rt87": "87", "rt22": "22", "rt370": "370", "rt286": "286", "rt289": "289"}
TOLERANCE = 0.00005  # degrees (~5 m) — visually identical at street zoom


def read_csv(z, name):
    return csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8-sig"))


def simplify(pts, tol):
    """Iterative Douglas-Peucker on [lat, lng] points."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (y1, x1), (y2, x2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        best, idx = 0.0, None
        for i in range(a + 1, b):
            y, x = pts[i]
            d = abs(dy * (x - x1) - dx * (y - y1)) / norm if (dx or dy) else math.hypot(x - x1, y - y1)
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


z = zipfile.ZipFile(GTFS)
short_to_id = {r["route_short_name"]: r["route_id"] for r in read_csv(z, "routes.txt")}
wanted = {short_to_id[s]: s for s in ROUTES.values() if s in short_to_id}

shape_counts = defaultdict(Counter)  # short_name -> Counter(shape_id)
for t in read_csv(z, "trips.txt"):
    if t["route_id"] in wanted and t.get("shape_id"):
        shape_counts[wanted[t["route_id"]]][t["shape_id"]] += 1

needed = {}
for short, counts in shape_counts.items():
    needed[short] = counts.most_common(1)[0][0]
needed_ids = set(needed.values())

raw = defaultdict(list)
for s in read_csv(z, "shapes.txt"):
    if s["shape_id"] in needed_ids:
        raw[s["shape_id"]].append((int(s["shape_pt_sequence"]), float(s["shape_pt_lat"]), float(s["shape_pt_lon"])))

with open(HTML, encoding="utf-8") as f:
    html = f.read()

for rid, short in ROUTES.items():
    if short not in needed:
        print(f"✗ {rid}: route {short} not found in GTFS — left unchanged")
        continue
    pts = [[lat, lng] for _, lat, lng in sorted(raw[needed[short]])]
    simp = [[round(a, 5), round(b, 5)] for a, b in simplify(pts, TOLERANCE)]
    js = "[" + ",".join(f"[{a},{b}]" for a, b in simp) + "]"
    pattern = re.compile(r'(id:"' + rid + r'",[^\n]*\n\s*path:)\[\[.*?\]\]')
    html, n = pattern.subn(lambda m: m.group(1) + js, html, count=1)
    print(f"{'✓' if n else '✗ anchor not found:'} {rid} (route {short}, shape {needed[short]}): {len(pts)} → {len(simp)} pts")

# ── Stops: CDTA timepoints (the major stops printed on timetables) ─────────
# One representative trip per route on its main shape; its timepoint stops
# become the map's stop markers. Extra context stops (routes with no drawn
# line) are snapped to the nearest real stop served by that route.
EXTRA_STOPS = [  # (route, approx lat, approx lng) from the original hand-made list
    ("13", 42.6534, -73.7769),  # Albany Medical Center
    ("12", 42.6658, -73.7854),  # Albany High School
    ("22", 42.6452, -73.7555),  # Warren St / EOC Albany South
]
stop_info = {s["stop_id"]: s for s in read_csv(z, "stops.txt")}
id_to_short = {v: k for k, v in short_to_id.items()}
extra_routes = {r for r, _, _ in EXTRA_STOPS}

rep_trip = {}            # short_name -> trip_id on main shape
trip_short = {}          # trip_id -> short_name, for extra-stop routes
for t in read_csv(z, "trips.txt"):
    short = id_to_short.get(t["route_id"])
    if short in needed and t.get("shape_id") == needed[short] and short not in rep_trip:
        rep_trip[short] = t["trip_id"]
    if short in extra_routes:
        trip_short[t["trip_id"]] = short
rep_ids = {v: k for k, v in rep_trip.items()}

timepoints = defaultdict(list)   # short -> [(seq, stop_id)]
served = defaultdict(set)        # short -> stop_ids (extra routes only)
for st in read_csv(z, "stop_times.txt"):
    tid = st["trip_id"]
    if tid in rep_ids and st.get("timepoint") == "1":
        timepoints[rep_ids[tid]].append((int(st["stop_sequence"]), st["stop_id"]))
    if tid in trip_short:
        served[trip_short[tid]].add(st["stop_id"])

stops_out, seen = [], set()
def add_stop(sid, short):
    if sid in seen:
        return
    seen.add(sid)
    s = stop_info[sid]
    stops_out.append((short, f'  {{lat:{float(s["stop_lat"]):.5f},lng:{float(s["stop_lon"]):.5f},'
                             f'name:{json.dumps(s["stop_name"])},route:"{short}"}},'))

for short in ROUTES.values():
    for _, sid in sorted(timepoints.get(short, [])):
        add_stop(sid, short)
    print(f"✓ route {short}: {len(timepoints.get(short, []))} timepoint stops")
for short, lat, lng in EXTRA_STOPS:
    if not served.get(short):
        print(f"✗ extra stop: route {short} not in GTFS — skipped")
        continue
    sid = min(served[short], key=lambda i: (float(stop_info[i]["stop_lat"]) - lat) ** 2 +
                                          (float(stop_info[i]["stop_lon"]) - lng) ** 2)
    add_stop(sid, short)
    print(f"✓ extra stop (route {short}): {stop_info[sid]['stop_name']}")

block_lines, last = ["const transitStops = [",
                     "  // CDTA GTFS timepoints — generated by build/extract_cdta_routes.py"], None
for short, line in stops_out:
    if short != last:
        block_lines.append(f"  // Route {short}")
        last = short
    block_lines.append(line)
block_lines.append("];")
html, n = re.subn(r"const transitStops = \[.*?\n\];", lambda m: "\n".join(block_lines), html, count=1, flags=re.S)
print(f"{'✓' if n else '✗ transitStops anchor not found'} transitStops: {len(stops_out)} stops")

with open(HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"✓ Wrote {HTML} — {os.path.getsize(HTML) // 1024}KB")
