"""NYC Subway Arrivals plugin implementation.

Displays upcoming train arrivals for a chosen NYC subway station, like the
countdown clocks in the station itself. Data comes from the MTA's public
GTFS-realtime subway feeds (no API key required since 2021).

The MTA splits subway data across 8 feed endpoints by line group. This plugin
resolves the configured station to its GTFS stop ids and fetches only the
feed(s) that actually serve it.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from src.plugins.base import PluginBase, PluginResult

from . import gtfs_realtime_NYCT_pb2  # noqa: F401 - registers NYCT extensions
from . import gtfs_realtime_pb2, stations

logger = logging.getLogger(__name__)

# Public, key-free MTA GTFS-realtime subway feeds.
FEED_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2F"

REQUEST_TIMEOUT = 10  # seconds, per feed
VALID_DIRECTIONS = ("north", "south", "both")
DIRECTION_SUFFIX = {"north": "N", "south": "S"}
MAX_LINES = 6  # board height
LINE_WIDTH = 22  # board width


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
        return str(value).strip().lower()

    def _get_route_filter(self) -> Optional[set]:
        raw = self.config.get("routes")
        if not raw:
            return None
        routes = {r.strip().upper() for r in str(raw).replace(",", " ").split() if r.strip()}
        return routes or None

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
        if direction not in VALID_DIRECTIONS:
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
        if direction not in VALID_DIRECTIONS:
            direction = "both"
        route_filter = self._get_route_filter()
        max_arrivals = self._get_max_arrivals()

        # Stop ids belonging to this station, and their direction labels.
        stop_ids = {s["stop_id"] for s in station["stops"]}
        labels = stations.direction_labels(station)

        try:
            arrivals = self._collect_arrivals(
                stations.feeds_for(station), stop_ids, direction, route_filter
            )
        except _AllFeedsFailed as exc:
            return PluginResult(available=False, error=str(exc))

        groups = self._group_arrivals(arrivals, labels, max_arrivals)
        data = self._build_data(station, groups)
        return PluginResult(
            available=True,
            data=data,
            formatted_lines=self._format_display(station, groups),
        )

    def _collect_arrivals(
        self,
        feeds: List[str],
        stop_ids: set,
        direction: str,
        route_filter: Optional[set],
    ) -> List[Dict[str, Any]]:
        """Fetch and parse the relevant feeds into a flat arrivals list.

        Raises ``_AllFeedsFailed`` if every feed request/parse fails.
        """
        now = datetime.now(timezone.utc).timestamp()
        arrivals: List[Dict[str, Any]] = []
        failures = 0

        for slug in feeds:
            try:
                feed = self._fetch_feed(slug)
            except Exception as exc:  # network or parse error
                failures += 1
                logger.warning("NYC Subway feed %s failed: %s", slug, exc)
                continue
            arrivals.extend(
                self._parse_feed(feed, stop_ids, direction, route_filter, now)
            )

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

            for stu in entity.trip_update.stop_time_update:
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
                out.append(
                    {"route": route, "direction": suffix,
                     "parent": parent, "eta": eta}
                )
        return out

    # ------------------------------------------------------------------ #
    # Shaping output
    # ------------------------------------------------------------------ #
    @staticmethod
    def _group_arrivals(
        arrivals: List[Dict[str, Any]],
        labels: Dict[str, Dict[str, str]],
        max_arrivals: int,
    ) -> List[Dict[str, Any]]:
        """Group arrivals by (route, direction), keeping the soonest ones."""
        grouped: Dict[tuple, Dict[str, Any]] = {}
        for arr in arrivals:
            key = (arr["route"], arr["direction"])
            group = grouped.get(key)
            if group is None:
                label = labels.get(arr["parent"], {}).get(arr["direction"], "")
                group = {
                    "route": arr["route"],
                    "direction": arr["direction"],
                    "label": label,
                    "etas": [],
                }
                grouped[key] = group
            if len(group["etas"]) < max_arrivals:
                group["etas"].append(arr["eta"])

        result = list(grouped.values())
        # Sort by soonest train, then route, for stable display.
        result.sort(key=lambda g: (g["etas"][0] if g["etas"] else 999, g["route"]))
        return result

    def _build_data(
        self, station: Dict[str, Any], groups: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Build the template-variable dictionary."""
        arrivals = []
        for group in groups:
            for eta in group["etas"]:
                arrivals.append({
                    "route": group["route"],
                    "direction": group["direction"],
                    "eta": eta,
                    "label": group["label"],
                })
        arrivals.sort(key=lambda a: a["eta"])

        if arrivals:
            nxt = arrivals[0]
            formatted = f"{nxt['route']} {nxt['label']}: {nxt['eta']} min"
        else:
            formatted = "No trains"

        return {
            "station_name": station["name"],
            "formatted": formatted[:LINE_WIDTH],
            "arrival_count": len(arrivals),
            "updated_at": datetime.now().strftime("%H:%M"),
            "arrivals": arrivals,
        }

    @staticmethod
    def _format_group_line(group: Dict[str, Any]) -> str:
        """Render one route+direction group as a board line, e.g. '1 N 2,5,9'."""
        etas = ",".join(str(e) for e in group["etas"])
        line = f"{group['route']} {group['direction']} {etas}"
        return line[:LINE_WIDTH]

    def _format_display(
        self, station: Dict[str, Any], groups: List[Dict[str, Any]]
    ) -> List[str]:
        """Format the station board for standalone display (6 lines)."""
        lines: List[str] = [station["name"][:LINE_WIDTH]]
        if not groups:
            lines.append("NO TRAINS")
        else:
            for group in groups[: MAX_LINES - 1]:
                lines.append(self._format_group_line(group))

        while len(lines) < MAX_LINES:
            lines.append("")
        return lines[:MAX_LINES]

    def cleanup(self) -> None:
        """Cleanup when the plugin is disabled. No persistent resources held."""
        logger.info("Plugin %s cleanup", self.plugin_id)


class _AllFeedsFailed(Exception):
    """Raised when every realtime feed request fails."""
