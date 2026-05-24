#!/usr/bin/env python3
"""Generate stations.py from the MTA "Subway Stations" open dataset.

The MTA realtime feeds reference stations by GTFS stop id. Users, however,
should configure a readable station name. This script downloads the official
station list and emits a committed ``stations.py`` data module.

Stations are grouped by *complex*: the dataset has one row per line/platform,
but a rider at "Times Sq-42 St" expects every train serving that complex.
Each complex records all of its GTFS stop ids and the realtime feed(s) that
carry them, so the plugin fetches exactly the feeds it needs.

Run from the repo root:  python scripts/generate_stations.py
The generated stations.py is committed; this script is not used at runtime.
"""

import csv
import io
import urllib.request
from collections import Counter
from pathlib import Path

# MTA "Subway Stations" dataset (data.ny.gov), CSV export.
CSV_URL = "https://data.ny.gov/api/views/39hk-dx4f/rows.csv?accessType=DOWNLOAD"

# Each subway line is published in one of 8 realtime feed endpoints.
ROUTE_TO_FEED = {
    "1": "gtfs", "2": "gtfs", "3": "gtfs", "4": "gtfs",
    "5": "gtfs", "6": "gtfs", "7": "gtfs",
    "A": "gtfs-ace", "C": "gtfs-ace", "E": "gtfs-ace",
    "B": "gtfs-bdfm", "D": "gtfs-bdfm", "F": "gtfs-bdfm", "M": "gtfs-bdfm",
    "G": "gtfs-g",
    "J": "gtfs-jz", "Z": "gtfs-jz",
    "N": "gtfs-nqrw", "Q": "gtfs-nqrw", "R": "gtfs-nqrw", "W": "gtfs-nqrw",
    "L": "gtfs-l",
    "SIR": "gtfs-si",
}


def feed_for_route(route: str, line: str) -> str:
    """Map a route label to its realtime feed slug.

    "S" is ambiguous: the 42 St Shuttle rides the numbered-lines feed, while
    the Franklin Av and Rockaway Park shuttles ride the ACE feed.
    """
    if route == "S":
        return "gtfs" if "42" in line else "gtfs-ace"
    return ROUTE_TO_FEED.get(route, "gtfs")


def build_complexes() -> dict:
    """Return {complex_id: complex_dict} grouped from the per-platform rows."""
    with urllib.request.urlopen(CSV_URL) as resp:  # noqa: S310 - trusted gov URL
        text = resp.read().decode("utf-8-sig")

    complexes: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text)):
        stop_id = row["GTFS Stop ID"].strip()
        complex_id = row["Complex ID"].strip()
        if not stop_id or not complex_id:
            continue
        line = row["Line"].strip()
        routes = [r for r in row["Daytime Routes"].split() if r]

        cx = complexes.setdefault(
            complex_id,
            {"names": [], "borough": row["Borough"].strip(),
             "routes": set(), "feeds": set(), "stops": []},
        )
        cx["names"].append(row["Stop Name"].strip())
        cx["routes"].update(routes)
        cx["feeds"].update(feed_for_route(r, line) for r in routes)
        cx["stops"].append({
            "stop_id": stop_id,
            "routes": routes,
            "north_label": row["North Direction Label"].strip(),
            "south_label": row["South Direction Label"].strip(),
        })
    return complexes


def finalize(complexes: dict) -> dict:
    """Collapse working sets into ordered, JSON-ish station records."""
    stations = {}
    for complex_id, cx in complexes.items():
        # Primary name: the most common stop name in the complex.
        name = Counter(cx["names"]).most_common(1)[0][0]
        aliases = sorted(set(cx["names"]))
        stations[complex_id] = {
            "name": name,
            "aliases": aliases,
            "borough": cx["borough"],
            "routes": sorted(cx["routes"]),
            "feeds": sorted(cx["feeds"]),
            "stops": sorted(cx["stops"], key=lambda s: s["stop_id"]),
        }
    return stations


HEADER = '''"""Bundled NYC subway station data — GENERATED, do not edit by hand.

Regenerate with: python scripts/generate_stations.py

STATIONS maps an MTA complex id to a station record. Stations are grouped by
complex, so one entry covers every platform/line a rider can reach without
leaving the station. Each record lists the GTFS stop ids and the realtime
feed(s) that carry them. Use resolve_station() to turn a user-supplied name
into a station.
"""

import difflib
'''

BODY = '''

# Number of distinct station complexes.
STATION_COUNT = len(STATIONS)

# Lowercased station name/alias -> list of complex ids.
_NAME_INDEX: dict[str, list[str]] = {}
for _cid, _meta in STATIONS.items():
    for _alias in {_meta["name"], *_meta["aliases"]}:
        _NAME_INDEX.setdefault(_alias.lower(), []).append(_cid)

# GTFS stop id -> complex id.
_STOP_INDEX: dict[str, str] = {}
for _cid, _meta in STATIONS.items():
    for _stop in _meta["stops"]:
        _STOP_INDEX[_stop["stop_id"]] = _cid


def _qualified(complex_id: str) -> str:
    """Human-readable, unambiguous label, e.g. '86 St (1)'."""
    meta = STATIONS[complex_id]
    return f"{meta['name']} ({' '.join(meta['routes'])})"


def _with_id(complex_id: str) -> dict:
    return {**STATIONS[complex_id], "complex_id": complex_id}


def resolve_station(query: str):
    """Resolve a user-supplied station name or GTFS stop id.

    Returns (station_dict, None) on success, where station_dict includes the
    resolved "complex_id". On failure returns (None, error_message) with close
    matches or disambiguation options where possible.
    """
    if not query or not query.strip():
        return None, "No station configured."
    q = query.strip()

    # Direct GTFS stop id. (Complex ids are internal and intentionally not
    # accepted as input: they share the numeric namespace with stop ids.)
    if q in _STOP_INDEX:
        return _with_id(_STOP_INDEX[q]), None

    # Route-qualified form: "86 St (1)".
    name = q
    want_routes = None
    if q.endswith(")") and "(" in q:
        name, _, rest = q.rpartition("(")
        name = name.strip()
        want_routes = rest.rstrip(")").split()

    matches = _NAME_INDEX.get(name.lower(), [])
    if want_routes:
        matches = [
            cid for cid in matches
            if set(STATIONS[cid]["routes"]) == set(want_routes)
        ]

    if len(matches) == 1:
        return _with_id(matches[0]), None

    if len(matches) > 1:
        options = ", ".join(sorted(_qualified(c) for c in matches))
        return None, f"Multiple stations named '{name}'. Use one of: {options}"

    # No match — suggest close names.
    close = difflib.get_close_matches(
        name.lower(), _NAME_INDEX.keys(), n=3, cutoff=0.6
    )
    if close:
        suggestions = ", ".join(
            STATIONS[_NAME_INDEX[c][0]]["name"] for c in close
        )
        return None, f"Unknown station '{query}'. Did you mean: {suggestions}?"
    return None, f"Unknown station '{query}'."


def feeds_for(station: dict) -> list[str]:
    """Realtime feed slugs that carry the given station's trains."""
    return station.get("feeds", [])


def direction_labels(station: dict) -> dict[str, dict[str, str]]:
    """Map each GTFS stop id to its {'N': label, 'S': label} direction names."""
    return {
        s["stop_id"]: {"N": s["north_label"], "S": s["south_label"]}
        for s in station.get("stops", [])
    }
'''


def fmt_stop(stop: dict) -> str:
    return (
        "{"
        f"\"stop_id\": {stop['stop_id']!r}, "
        f"\"routes\": {stop['routes']!r}, "
        f"\"north_label\": {stop['north_label']!r}, "
        f"\"south_label\": {stop['south_label']!r}"
        "}"
    )


def main() -> None:
    stations = finalize(build_complexes())
    repo_root = Path(__file__).resolve().parent.parent
    out = repo_root / "plugins" / "nyc_subway" / "stations.py"

    lines = [HEADER, "\nSTATIONS = {"]
    for complex_id in sorted(stations):
        m = stations[complex_id]
        stops = ", ".join(fmt_stop(s) for s in m["stops"])
        lines.append(
            f"    {complex_id!r}: {{"
            f"\"name\": {m['name']!r}, "
            f"\"aliases\": {m['aliases']!r}, "
            f"\"borough\": {m['borough']!r}, "
            f"\"routes\": {m['routes']!r}, "
            f"\"feeds\": {m['feeds']!r}, "
            f"\"stops\": [{stops}]}},"
        )
    lines.append("}")
    lines.append(BODY)

    out.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out} with {len(stations)} station complexes.")


if __name__ == "__main__":
    main()
