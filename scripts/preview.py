#!/usr/bin/env python3
"""Render the plugin against the live MTA feeds, no host UI required.

Fetches real arrivals for a station and prints what each board size would
show, plus the direction labels. Handy for eyeballing a change before wiring
it into FiestaBoard.

    python scripts/preview.py "46 St (M R)"
    python scripts/preview.py "Times Sq-42 St (1 2 3 7 A C E N Q R S W)"

Needs the FiestaBoard host importable — same as the tests. Point at your
checkout with FIESTABOARD_PATH or the .fiestaboard_path.local marker file.
"""

import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _host_path() -> Path:
    env = os.environ.get("FIESTABOARD_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    marker = _REPO / ".fiestaboard_path.local"
    if marker.exists() and marker.read_text().strip():
        return Path(marker.read_text().strip()).expanduser()
    sys.exit("Set FIESTABOARD_PATH or create .fiestaboard_path.local (see conftest.py).")


sys.path.insert(0, str(_host_path()))
sys.path.insert(0, str(_REPO))

from src.devices import BoardContext  # noqa: E402
from plugins.nyc_subway.plugin import NycSubwayPlugin  # noqa: E402


def main() -> None:
    station = sys.argv[1] if len(sys.argv) > 1 else "46 St (M R)"
    manifest = json.loads((_REPO / "manifest.json").read_text())
    plugin = NycSubwayPlugin(manifest)
    plugin.config = {"station": station}

    result = plugin.fetch_data()
    if not result.available:
        sys.exit(f"unavailable: {result.error}")

    data = result.data
    print(f"\n{data['station_name']}   ({data['arrival_count']} arrivals)")
    print(f"  uptown  : {data['uptown_label']}")
    print(f"  downtown: {data['downtown_label']}")
    print(f"  status  : {data['line_status']}\n")

    seen = set()
    for arr in data["arrivals"]:
        key = (arr["route"], arr["direction_short"])
        if key in seen:
            continue
        seen.add(key)
        print(f"  {arr['route']:>2} {arr['direction_short']:>4} -> "
              f"{arr['direction_label']:<18} (to {arr['terminus']})")

    for device_type, width in (("flagship", 22), ("note", 15)):
        plugin.clear_cache()
        with plugin._bound_board(BoardContext.from_device_type(device_type)):
            lines = plugin.fetch_data().formatted_lines
        print(f"\n  {device_type.upper()} ({width} wide):")
        for line in lines:
            print(f"    |{line:<{width}}|")


if __name__ == "__main__":
    main()
