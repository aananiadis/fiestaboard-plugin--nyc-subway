"""NYC Subway Arrivals plugin implementation.

Displays upcoming train arrivals for a chosen NYC subway station, like the
countdown clocks in the station itself. Data comes from the MTA's public
GTFS-realtime subway feeds (no API key required since 2021).

The MTA splits subway data across 8 feed endpoints by line group. This plugin
resolves the configured station to its GTFS stop ids, then reads every feed —
a rerouted train stops at stations its line doesn't normally serve, and it
stays in its own line group's feed while doing so.
"""

import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from src.plugins.base import PluginBase, PluginResult

from . import gtfs_realtime_NYCT_pb2  # noqa: F401 - registers NYCT extensions
from . import gtfs_realtime_pb2, stations

logger = logging.getLogger(__name__)

# Public, key-free MTA GTFS-realtime subway feeds.
FEED_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2F"
ALERTS_URL = (
    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"
)

# Every MTA subway feed. We fetch all of them rather than only the feeds for a
# station's *scheduled* routes: during reroutes a train shows up at a station
# its line doesn't normally serve (e.g. E trains running local in Queens stop
# at 46 St, which is scheduled M/R only). Those trains stay in their own line
# group's feed, so scoping the fetch to scheduled routes silently drops them.
ALL_FEEDS = (
    "gtfs",       # 1234567S
    "gtfs-ace",
    "gtfs-bdfm",
    "gtfs-g",
    "gtfs-jz",
    "gtfs-l",
    "gtfs-nqrw",
    "gtfs-si",
)

REQUEST_TIMEOUT = 10  # seconds, per feed
MAX_FEED_WORKERS = 8  # fetch the feeds concurrently — they are independent

# Accept long and short forms — boards with narrow columns can ask for "up"/"down".
DIRECTION_ALIASES = {
    "both": "both", "all": "both",
    "uptown": "uptown", "up": "uptown", "north": "uptown", "northbound": "uptown",
    "downtown": "downtown", "down": "downtown", "south": "downtown", "southbound": "downtown",
}
VALID_DIRECTIONS = ("both", "uptown", "downtown", "up", "down")
DIRECTION_SUFFIX = {"uptown": "N", "downtown": "S"}
DIRECTION_NAME = {"N": "uptown", "S": "downtown"}
DIRECTION_SHORT = {"N": "up", "S": "down"}

# MTA's per-platform labels are sometimes generic ("Outbound", "Uptown", etc.) —
# when they are, the train's terminus is a more useful friendly name.
_GENERIC_LABELS = {
    "uptown", "downtown", "northbound", "southbound",
    "eastbound", "westbound", "inbound", "outbound", "last stop",
}

# Per-route Vestaboard tile color, matching the MTA's official line bullets.
# Routes whose true color (brown, gray) isn't in the Vestaboard palette fall
# back to "white" rather than a misleading substitute.
ROUTE_COLORS = {
    "1": "red", "2": "red", "3": "red",
    "4": "green", "5": "green", "6": "green", "6X": "green",
    "7": "violet", "7X": "violet",
    "A": "blue", "C": "blue", "E": "blue",
    "B": "orange", "D": "orange", "F": "orange", "FX": "orange", "M": "orange",
    "G": "green",
    "N": "yellow", "Q": "yellow", "R": "yellow", "W": "yellow",
    "J": "white", "Z": "white",   # MTA brown — no brown tile
    "L": "white",                  # MTA light slate gray — no gray tile
    "S": "white", "H": "white", "FS": "white", "GS": "white",
    "SI": "blue", "SIR": "blue",
}

# Flagship board geometry. Used when the plugin renders outside a board-scoped
# call (``self.board`` is None); otherwise the real board's size wins, so a
# Note (3x15) gets lines that actually fit.
MAX_LINES = 6  # board height
LINE_WIDTH = 22  # board width

# A destination is only worth printing if this much room is left for it.
MIN_DEST_WIDTH = 6

# Cap for direction labels, matching `max_lengths` in manifest.json.
LABEL_WIDTH = 22

# GTFS-realtime Alert.Effect enum -> status color.
# Anything not listed (incl. ADDITIONAL_SERVICE, UNKNOWN_EFFECT, no alert) = green.
STATUS_GREEN = "green"
STATUS_YELLOW = "yellow"
STATUS_RED = "red"
_STATUS_RANK = {STATUS_GREEN: 0, STATUS_YELLOW: 1, STATUS_RED: 2}
_EFFECT_STATUS = {
    1: STATUS_RED,     # NO_SERVICE
    2: STATUS_RED,     # REDUCED_SERVICE
    3: STATUS_RED,     # SIGNIFICANT_DELAYS
    4: STATUS_YELLOW,  # DETOUR
    6: STATUS_YELLOW,  # MODIFIED_SERVICE
    7: STATUS_YELLOW,  # OTHER_EFFECT
    9: STATUS_YELLOW,  # STOP_MOVED
}


def _color_for_route(route: str) -> str:
    return ROUTE_COLORS.get((route or "").upper(), "white")


def _arrival_key(arrival: Dict[str, Any]) -> tuple:
    """Identity of one train calling at one platform, for de-duplication.

    A trip id pins a train exactly. Feeds that omit it fall back to the
    train's route and time, which is as unique as a platform can get.
    """
    if arrival["trip_id"]:
        return (arrival["trip_id"], arrival["parent"], arrival["suffix"])
    return (
        arrival["route"],
        arrival["parent"],
        arrival["suffix"],
        arrival["eta"],
    )


def _short_place(name: str) -> str:
    """Shorten a terminus to the place riders name a direction by.

    MTA signage says trains run to "Forest Hills", not "Forest Hills-71 Av".
    Station names pair a place with a cross street; keep the place half —
    unless the place half *is* the street ("34 St-Hudson Yards"), in which
    case the other half is the name people use.
    """
    head, sep, tail = (name or "").partition("-")
    if not sep:
        return name
    head, tail = head.strip(), tail.strip()
    if head[:1].isdigit() and tail:
        return tail
    return head or name


class NycSubwayPlugin(PluginBase):
    """NYC Subway arrival-times plugin.

    Fetches GTFS-realtime ``TripUpdate`` data for the configured station and
    groups upcoming arrivals by route and direction.
    """

    @property
    def plugin_id(self) -> str:
        """Return the plugin ID matching manifest.json."""
        return "nyc_subway"

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #
    def _get_station_query(self) -> str:
        return (self.config.get("station") or os.getenv("NYC_SUBWAY_STATION") or "").strip()

    def _get_direction(self) -> str:
        value = (self.config.get("direction") or os.getenv("NYC_SUBWAY_DIRECTION") or "both")
        return DIRECTION_ALIASES.get(str(value).strip().lower(), "both")

    def _get_route_filter(self) -> Optional[set]:
        raw = self.config.get("routes")
        if not raw:
            return None
        routes = {r.strip().upper() for r in str(raw).replace(",", " ").split() if r.strip()}
        return routes or None

    def _get_show_alerts(self) -> bool:
        value = self.config.get("show_alerts")
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip().lower() not in ("false", "0", "no", "off", "")
        return bool(value)

    def _get_max_arrivals(self) -> int:
        try:
            value = int(self.config.get("max_arrivals") or 3)
        except (TypeError, ValueError):
            return 3
        return max(1, min(value, 6))

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate plugin configuration. Returns a list of error messages."""
        errors: List[str] = []

        station = (config.get("station") or os.getenv("NYC_SUBWAY_STATION") or "").strip()
        if not station:
            errors.append("Station is required")
        else:
            _, error = stations.resolve_station(station)
            if error:
                errors.append(error)

        direction = str(config.get("direction") or "both").strip().lower()
        if direction not in DIRECTION_ALIASES:
            errors.append(
                f"Direction must be one of: {', '.join(VALID_DIRECTIONS)}"
            )

        return errors

    # ------------------------------------------------------------------ #
    # Data fetching
    # ------------------------------------------------------------------ #
    def fetch_data(self) -> PluginResult:
        """Fetch upcoming arrivals for the configured station."""
        query = self._get_station_query()
        if not query:
            return PluginResult(available=False, error="No station configured")

        station, error = stations.resolve_station(query)
        if error:
            return PluginResult(available=False, error=error)

        direction = self._get_direction()
        route_filter = self._get_route_filter()
        max_arrivals = self._get_max_arrivals()

        # Stop ids belonging to this station, and their direction labels.
        stop_ids = {s["stop_id"] for s in station["stops"]}
        labels = stations.direction_labels(station)

        try:
            arrivals = self._collect_arrivals(
                ALL_FEEDS, stop_ids, direction, route_filter
            )
        except _AllFeedsFailed as exc:
            return PluginResult(available=False, error=str(exc))

        # Routes we report status for: the ones scheduled here, plus any route
        # actually running to this station right now (a rerouted E is real
        # service and deserves a real status).
        status_routes = {r.upper() for r in station.get("routes", [])}
        status_routes |= {a["route"].upper() for a in arrivals}
        if route_filter:
            status_routes &= route_filter
        statuses = (
            self._fetch_route_statuses(status_routes)
            if self._get_show_alerts() and status_routes
            else {r: STATUS_GREEN for r in status_routes}
        )

        groups = self._group_arrivals(arrivals, labels, max_arrivals)
        direction_labels = self._direction_labels(station, groups)
        data = self._build_data(station, groups, statuses, direction_labels)
        return PluginResult(
            available=True,
            data=data,
            formatted_lines=self._format_display(station, groups),
        )

    def _collect_arrivals(
        self,
        feeds,
        stop_ids: set,
        direction: str,
        route_filter: Optional[set],
    ) -> List[Dict[str, Any]]:
        """Fetch and parse every feed into a flat arrivals list.

        Feeds are fetched concurrently — they are independent requests, and
        doing all eight in series would blow past a sane refresh budget.

        Raises ``_AllFeedsFailed`` if every feed request/parse fails.
        """
        now = datetime.now(timezone.utc).timestamp()
        feeds = list(feeds)
        arrivals: List[Dict[str, Any]] = []
        failures = 0

        def fetch(slug: str):
            try:
                return self._fetch_feed(slug)
            except Exception as exc:  # network or parse error
                logger.warning("NYC Subway feed %s failed: %s", slug, exc)
                return None

        # One train must be listed once, even if two feeds carry its trip.
        seen: set = set()

        workers = min(MAX_FEED_WORKERS, len(feeds)) or 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for feed in pool.map(fetch, feeds):
                if feed is None:
                    failures += 1
                    continue
                for arrival in self._parse_feed(
                    feed, stop_ids, direction, route_filter, now
                ):
                    key = _arrival_key(arrival)
                    if key in seen:
                        continue
                    seen.add(key)
                    arrivals.append(arrival)

        if feeds and failures == len(feeds):
            raise _AllFeedsFailed("Unable to reach MTA realtime feeds")

        arrivals.sort(key=lambda a: a["eta"])
        return arrivals

    def _fetch_feed(self, slug: str):
        """Fetch and parse a single GTFS-realtime feed."""
        response = requests.get(FEED_BASE + slug, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
        return feed

    def _fetch_route_statuses(self, routes: set) -> Dict[str, str]:
        """Return {route: status_color} for the given routes, based on MTA alerts.

        Falls back to all-green if the alerts feed is unreachable.
        """
        statuses = {r: STATUS_GREEN for r in routes}
        try:
            response = requests.get(ALERTS_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
        except Exception as exc:
            logger.warning("NYC Subway alerts feed failed: %s", exc)
            return statuses

        now = datetime.now(timezone.utc).timestamp()
        for entity in feed.entity:
            if not entity.HasField("alert"):
                continue
            alert = entity.alert
            if not _alert_is_active(alert, now):
                continue
            status = _EFFECT_STATUS.get(alert.effect)
            if not status:
                continue
            for informed in alert.informed_entity:
                route = (informed.route_id or "").upper()
                if route in statuses and _STATUS_RANK[status] > _STATUS_RANK[statuses[route]]:
                    statuses[route] = status
        return statuses

    @staticmethod
    def _parse_feed(
        feed,
        stop_ids: set,
        direction: str,
        route_filter: Optional[set],
        now: float,
    ) -> List[Dict[str, Any]]:
        """Extract this station's upcoming arrivals from one feed message."""
        wanted_suffix = DIRECTION_SUFFIX.get(direction)  # None means "both"
        out: List[Dict[str, Any]] = []

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            route = entity.trip_update.trip.route_id
            if route_filter and route.upper() not in route_filter:
                continue
            trip_id = entity.trip_update.trip.trip_id

            stus = list(entity.trip_update.stop_time_update)
            # The terminus is the last stop in the trip — riders read this as
            # "F to Jamaica-179 St", same as platform signs and announcements.
            terminus_stop = stus[-1].stop_id if stus else ""
            terminus_name = stations.station_name_for_stop(terminus_stop) or ""

            for stu in stus:
                stop_id = stu.stop_id
                if not stop_id:
                    continue
                suffix = stop_id[-1]
                parent = stop_id[:-1]
                if parent not in stop_ids or suffix not in ("N", "S"):
                    continue
                if wanted_suffix and suffix != wanted_suffix:
                    continue

                when = 0
                if stu.HasField("arrival") and stu.arrival.time:
                    when = stu.arrival.time
                elif stu.HasField("departure") and stu.departure.time:
                    when = stu.departure.time
                if not when:
                    continue

                eta = int(round((when - now) / 60.0))
                if eta < 0:
                    continue
                out.append({
                    "route": route,
                    "trip_id": trip_id,
                    "suffix": suffix,
                    "parent": parent,
                    "eta": eta,
                    "terminus": terminus_name,
                })
        return out

    # ------------------------------------------------------------------ #
    # Shaping output
    # ------------------------------------------------------------------ #
    @staticmethod
    def _friendly_label(raw_label: str, terminus: str) -> str:
        """Replace a generic MTA platform label with the terminus name when possible."""
        if raw_label and raw_label.strip().lower() not in _GENERIC_LABELS:
            return raw_label
        return terminus or raw_label

    @classmethod
    def _group_arrivals(
        cls,
        arrivals: List[Dict[str, Any]],
        labels: Dict[str, Dict[str, str]],
        max_arrivals: int,
    ) -> List[Dict[str, Any]]:
        """Group arrivals by (route, direction, terminus), keeping the soonest ones."""
        grouped: Dict[tuple, Dict[str, Any]] = {}
        for arr in arrivals:
            suffix = arr["suffix"]
            terminus = arr.get("terminus") or ""
            key = (arr["route"], suffix, terminus)
            group = grouped.get(key)
            if group is None:
                raw = labels.get(arr["parent"], {}).get(suffix, "")
                group = {
                    "route": arr["route"],
                    "suffix": suffix,
                    "direction": DIRECTION_NAME[suffix],
                    "direction_short": DIRECTION_SHORT[suffix],
                    "terminus": terminus,
                    "label": cls._friendly_label(raw, terminus),
                    "color": _color_for_route(arr["route"]),
                    "etas": [],
                }
                grouped[key] = group
            if len(group["etas"]) < max_arrivals:
                group["etas"].append(arr["eta"])

        result = list(grouped.values())
        # Sort by soonest train, then route, for stable display.
        result.sort(key=lambda g: (g["etas"][0] if g["etas"] else 999, g["route"]))
        return result

    @staticmethod
    def _direction_labels(
        station: Dict[str, Any], groups: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """Station-wide name for each direction, e.g. {'N': 'Forest Hills', 'S': 'Manhattan'}.

        Prefers the MTA's own platform label ("Manhattan", "The Bronx"). When
        that label is generic ("Outbound", "Uptown"), names the direction after
        where its trains actually go — the terminus serving the most upcoming
        trains, ties going to the soonest. That's how the platform signs read,
        and how the MTA app labels 46 St as "Forest Hills" / "Manhattan".

        A big complex has no single answer: Times Sq's northbound 7 platform
        points to Queens while its 1 platform points uptown. When the platforms
        disagree, the plain compass direction is the only honest label — the
        per-train ``direction_label`` still names each train's own platform.
        """
        stops = station.get("stops", [])
        labels: Dict[str, str] = {}
        for suffix, key in (("N", "north_label"), ("S", "south_label")):
            specific = [
                stop[key]
                for stop in stops
                if stop.get(key)
                and stop[key].strip().lower() not in _GENERIC_LABELS
            ]
            # Speak for the whole station only when every platform agrees.
            if specific and len(specific) == len(stops) and len(set(specific)) == 1:
                labels[suffix] = specific[0][:LABEL_WIDTH]
                continue
            # Platforms disagree (or some are generic): no station-wide answer.
            if len(stops) != 1:
                labels[suffix] = DIRECTION_NAME[suffix].title()
                continue

            # One platform, generic label ("Outbound") — name it by terminus.
            weight: Counter = Counter()
            soonest: Dict[str, int] = {}
            for group in groups:
                terminus = group["terminus"]
                if group["suffix"] != suffix or not terminus:
                    continue
                weight[terminus] += len(group["etas"])
                eta = group["etas"][0] if group["etas"] else 999
                soonest[terminus] = min(soonest.get(terminus, 999), eta)

            if weight:
                terminus = max(
                    weight.items(), key=lambda kv: (kv[1], -soonest[kv[0]])
                )[0]
                labels[suffix] = _short_place(terminus)[:LABEL_WIDTH]
            else:
                labels[suffix] = DIRECTION_NAME[suffix].title()
        return labels

    def _build_data(
        self,
        station: Dict[str, Any],
        groups: List[Dict[str, Any]],
        statuses: Dict[str, str],
        direction_labels: Dict[str, str],
    ) -> Dict[str, Any]:
        """Build the template-variable dictionary."""
        width = self._board_width()
        arrivals = []
        for group in groups:
            route_status = statuses.get(group["route"].upper(), STATUS_GREEN)
            # Name the direction by the platform this train actually leaves from,
            # so the 7 at Times Sq reads "Queens" while the 1 reads uptown.
            direction_label = (
                _short_place(group["label"])[:LABEL_WIDTH]
                or direction_labels.get(group["suffix"], "")
            )
            for eta in group["etas"]:
                arrivals.append({
                    "route": group["route"],
                    "direction": group["direction"],
                    "direction_short": group["direction_short"],
                    "direction_label": direction_label,
                    "eta": eta,
                    "label": group["label"],
                    "terminus": group["terminus"],
                    "color": group["color"],
                    "status": route_status,
                })
        arrivals.sort(key=lambda a: a["eta"])

        if arrivals:
            nxt = arrivals[0]
            dest = nxt["terminus"] or nxt["label"]
            formatted = f"{nxt['route']} to {dest}: {nxt['eta']}m"
        else:
            formatted = "No trains"

        line_statuses = [
            {"route": route, "status": statuses[route]}
            for route in sorted(statuses)
        ]
        overall = STATUS_GREEN
        for status in statuses.values():
            if _STATUS_RANK[status] > _STATUS_RANK[overall]:
                overall = status

        return {
            "station_name": station["name"],
            "formatted": formatted[:width],
            "arrival_count": len(arrivals),
            "updated_at": datetime.now().strftime("%H:%M"),
            "arrivals": arrivals,
            "uptown_label": direction_labels.get("N", ""),
            "downtown_label": direction_labels.get("S", ""),
            "line_status": overall,
            "line_statuses": line_statuses,
        }

    # ------------------------------------------------------------------ #
    # Board-aware display
    # ------------------------------------------------------------------ #
    def _board_width(self) -> int:
        """Width of the board being rendered on; Flagship's 22 outside a render."""
        board = self.board
        return board.width if board else LINE_WIDTH

    def _board_height(self) -> int:
        """Height of the board being rendered on; Flagship's 6 outside a render."""
        board = self.board
        return board.height if board else MAX_LINES

    @staticmethod
    def _format_group_line(group: Dict[str, Any], width: int) -> str:
        """Render one route+direction group as a board line, e.g. 'F Jamaica 2,5,9'.

        The destination is dropped rather than shaved to a stub when the board
        is too narrow to hold a meaningful piece of it (a Note's 15 columns).
        """
        etas = ",".join(str(e) for e in group["etas"])
        head = f"{group['route']} {etas}"
        dest = group.get("terminus") or group.get("label") or ""

        room = width - len(head) - 1  # the space before the etas
        if dest and room >= MIN_DEST_WIDTH:
            return f"{group['route']} {dest[:room]} {etas}"
        return head[:width]

    def _format_display(
        self, station: Dict[str, Any], groups: List[Dict[str, Any]]
    ) -> List[str]:
        """Format the station board for standalone display, sized to the board."""
        width = self._board_width()
        height = self._board_height()

        lines: List[str] = [station["name"][:width]]
        if not groups:
            lines.append("NO TRAINS")
        else:
            for group in groups[: height - 1]:
                lines.append(self._format_group_line(group, width))

        while len(lines) < height:
            lines.append("")
        return lines[:height]

    def cleanup(self) -> None:
        """Cleanup when the plugin is disabled. No persistent resources held."""
        logger.info("Plugin %s cleanup", self.plugin_id)


class _AllFeedsFailed(Exception):
    """Raised when every realtime feed request fails."""


def _alert_is_active(alert, now: float) -> bool:
    """Return True if the alert is currently in effect (or has no time bounds)."""
    if not alert.active_period:
        return True
    for period in alert.active_period:
        start = period.start if period.HasField("start") else 0
        end = period.end if period.HasField("end") else 0
        if start and now < start:
            continue
        if end and now > end:
            continue
        return True
    return False
