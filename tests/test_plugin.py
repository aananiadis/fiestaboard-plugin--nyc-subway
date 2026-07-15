"""Tests for the NYC Subway Arrivals plugin."""

import json
import time
from pathlib import Path

import pytest

from plugins.nyc_subway import NycSubwayPlugin
from plugins.nyc_subway import gtfs_realtime_pb2 as pb
from plugins.nyc_subway import gtfs_realtime_NYCT_pb2 as nyct
from plugins.nyc_subway import plugin as plugin_module
from plugins.nyc_subway import stations as stations_module
from src.plugins.base import PluginResult
from src.plugins.manifest import PluginManifest

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"

# A single-feed station keeps fixtures simple: Bedford Av is L-train only.
TEST_STATION = "Bedford Av"


@pytest.fixture
def manifest_data():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


@pytest.fixture
def manifest(manifest_data):
    return manifest_data


@pytest.fixture
def plugin(manifest):
    return NycSubwayPlugin(manifest)


@pytest.fixture
def station_stop():
    """Return (station_record, primary_stop_id) for the test station."""
    station, error = stations_module.resolve_station(TEST_STATION)
    assert error is None, error
    return station, station["stops"][0]["stop_id"]


def _build_feed(arrivals, use_departure=False, with_nyct=False):
    """Serialize a GTFS-realtime FeedMessage.

    arrivals: list of (route_id, stop_id, seconds_from_now) tuples.
    """
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "1.0"
    now = time.time()
    for index, (route, stop_id, offset) in enumerate(arrivals):
        entity = feed.entity.add()
        entity.id = str(index)
        trip_update = entity.trip_update
        trip_update.trip.route_id = route
        if with_nyct:
            ext = trip_update.trip.Extensions[nyct.nyct_trip_descriptor]
            ext.direction = (
                nyct.NyctTripDescriptor.NORTH
                if stop_id.endswith("N")
                else nyct.NyctTripDescriptor.SOUTH
            )
        stu = trip_update.stop_time_update.add()
        stu.stop_id = stop_id
        when = int(now + offset)
        if use_departure:
            stu.departure.time = when
        else:
            stu.arrival.time = when
    return feed.SerializeToString()


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def _patch_feed(monkeypatch, payload):
    """Make every feed request return the given serialized payload."""
    def fake_get(url, timeout=None):
        if isinstance(payload, Exception):
            raise payload
        return _FakeResponse(payload)

    monkeypatch.setattr(plugin_module.requests, "get", fake_get)


def _patch_feeds_by_slug(monkeypatch, payloads):
    """Serve a different payload per feed slug; every other feed comes back empty.

    Mirrors the real MTA split, where a trip lives in exactly one feed — so a
    route the plugin never fetches is genuinely invisible.
    """
    empty = _build_feed([])
    requested = []

    def fake_get(url, timeout=None):
        slug = url.rsplit("%2F", 1)[-1]
        requested.append(slug)
        return _FakeResponse(payloads.get(slug, empty))

    monkeypatch.setattr(plugin_module.requests, "get", fake_get)
    return requested


# --------------------------------------------------------------------- #
# Basic contract
# --------------------------------------------------------------------- #
class TestPluginContract:
    def test_plugin_id_matches_manifest(self, plugin, manifest_data):
        assert plugin.plugin_id == "nyc_subway"
        assert plugin.plugin_id == manifest_data["id"]

    def test_cleanup_runs(self, plugin):
        plugin.cleanup()  # must not raise


# --------------------------------------------------------------------- #
# Configuration validation
# --------------------------------------------------------------------- #
class TestValidateConfig:
    def test_missing_station(self, plugin):
        errors = plugin.validate_config({})
        assert any("Station" in e for e in errors)

    def test_unknown_station(self, plugin):
        errors = plugin.validate_config({"station": "Nowhere Junction"})
        assert len(errors) > 0

    def test_invalid_direction(self, plugin):
        errors = plugin.validate_config(
            {"station": TEST_STATION, "direction": "sideways"}
        )
        assert any("Direction" in e for e in errors)

    def test_valid_config(self, plugin):
        errors = plugin.validate_config(
            {"station": TEST_STATION, "direction": "both"}
        )
        assert errors == []


# --------------------------------------------------------------------- #
# fetch_data
# --------------------------------------------------------------------- #
class TestFetchData:
    def test_no_station_configured(self, plugin):
        plugin.config = {}
        result = plugin.fetch_data()
        assert result.available is False
        assert result.error

    def test_unknown_station(self, plugin):
        plugin.config = {"station": "Nowhere Junction"}
        result = plugin.fetch_data()
        assert result.available is False

    def test_success_returns_arrivals(self, plugin, station_stop, monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([
            ("L", stop_id + "N", 120),
            ("L", stop_id + "N", 360),
            ("L", stop_id + "S", 240),
        ]))
        plugin.config = {"station": TEST_STATION, "direction": "both"}
        result = plugin.fetch_data()

        assert result.available is True
        assert isinstance(result, PluginResult)
        assert result.data["arrival_count"] == 3
        assert result.data["station_name"]
        assert all(a["route"] == "L" for a in result.data["arrivals"])
        assert result.data["arrivals"][0]["eta"] <= result.data["arrivals"][-1]["eta"]

    def test_formatted_lines_is_six_lines(self, plugin, station_stop, monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([("L", stop_id + "N", 90)]))
        plugin.config = {"station": TEST_STATION}
        result = plugin.fetch_data()
        assert isinstance(result.formatted_lines, list)
        assert len(result.formatted_lines) == 6

    def test_manifest_variables_present_in_data(self, plugin, station_stop,
                                                manifest_data, monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([("L", stop_id + "N", 120)]))
        plugin.config = {"station": TEST_STATION}
        result = plugin.fetch_data()
        for var in manifest_data["variables"]["simple"]:
            assert var in result.data
        assert "arrivals" in result.data

    def test_direction_filter(self, plugin, station_stop, monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([
            ("L", stop_id + "N", 120),
            ("L", stop_id + "S", 180),
        ]))
        plugin.config = {"station": TEST_STATION, "direction": "uptown"}
        result = plugin.fetch_data()
        assert result.available is True
        assert all(a["direction"] == "uptown" for a in result.data["arrivals"])
        assert all(a["direction_short"] == "up" for a in result.data["arrivals"])

    def test_direction_short_form_accepted(self, plugin, station_stop, monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([
            ("L", stop_id + "N", 120),
            ("L", stop_id + "S", 180),
        ]))
        plugin.config = {"station": TEST_STATION, "direction": "down"}
        result = plugin.fetch_data()
        assert result.available is True
        assert all(a["direction"] == "downtown" for a in result.data["arrivals"])

    def test_terminus_resolved_from_last_stop(self, plugin, station_stop, monkeypatch):
        _, stop_id = station_stop
        # A single trip with multiple stop_time_updates — the terminus is the
        # last stop_id (Canarsie-Rockaway Pkwy, parent stop id L29).
        now = time.time()
        feed = pb.FeedMessage()
        feed.header.gtfs_realtime_version = "1.0"
        entity = feed.entity.add()
        entity.id = "0"
        entity.trip_update.trip.route_id = "L"
        for sid, offset in [(stop_id + "S", 120), ("L28S", 600), ("L29S", 1200)]:
            stu = entity.trip_update.stop_time_update.add()
            stu.stop_id = sid
            stu.arrival.time = int(now + offset)
        _patch_feed(monkeypatch, feed.SerializeToString())

        plugin.config = {"station": TEST_STATION, "direction": "downtown"}
        result = plugin.fetch_data()
        assert result.data["arrivals"][0]["terminus"] == "Canarsie-Rockaway Pkwy"

    def test_route_filter_excludes_other_routes(self, plugin, station_stop,
                                                monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([
            ("L", stop_id + "N", 120),
            ("Z", stop_id + "N", 180),
        ]))
        plugin.config = {"station": TEST_STATION, "routes": "L"}
        result = plugin.fetch_data()
        assert {a["route"] for a in result.data["arrivals"]} == {"L"}

    def test_past_and_other_station_arrivals_dropped(self, plugin, station_stop,
                                                     monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([
            ("L", stop_id + "N", -120),     # already departed
            ("L", "ZZZ9N", 200),            # different station
            ("L", stop_id + "N", 300),      # valid
        ]))
        plugin.config = {"station": TEST_STATION}
        result = plugin.fetch_data()
        assert result.data["arrival_count"] == 1

    def test_departure_time_used_when_no_arrival(self, plugin, station_stop,
                                                 monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch,
                    _build_feed([("L", stop_id + "N", 240)], use_departure=True))
        plugin.config = {"station": TEST_STATION}
        result = plugin.fetch_data()
        assert result.data["arrival_count"] == 1

    def test_nyct_extension_payload_parses(self, plugin, station_stop, monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch,
                    _build_feed([("L", stop_id + "N", 120)], with_nyct=True))
        plugin.config = {"station": TEST_STATION}
        result = plugin.fetch_data()
        assert result.available is True

    def test_no_trains(self, plugin, station_stop, monkeypatch):
        _patch_feed(monkeypatch, _build_feed([]))
        plugin.config = {"station": TEST_STATION}
        result = plugin.fetch_data()
        assert result.available is True
        assert result.data["arrival_count"] == 0
        assert result.data["formatted"] == "No trains"
        assert "NO TRAINS" in result.formatted_lines

    def test_max_arrivals_caps_per_group(self, plugin, station_stop, monkeypatch):
        _, stop_id = station_stop
        _patch_feed(monkeypatch, _build_feed([
            ("L", stop_id + "N", offset) for offset in (60, 120, 180, 240, 300)
        ]))
        plugin.config = {"station": TEST_STATION, "max_arrivals": 2}
        result = plugin.fetch_data()
        assert result.data["arrival_count"] == 2

    def test_all_feeds_fail(self, plugin, monkeypatch):
        _patch_feed(monkeypatch, ConnectionError("network down"))
        plugin.config = {"station": TEST_STATION}
        result = plugin.fetch_data()
        assert result.available is False
        assert result.error


# --------------------------------------------------------------------- #
# Manifest metadata
# --------------------------------------------------------------------- #
class TestManifest:
    def test_manifest_is_valid_json(self, manifest_data):
        assert manifest_data["id"] == "nyc_subway"

    def test_manifest_parses(self, manifest_data):
        parsed = PluginManifest.from_dict(manifest_data)
        assert parsed.id == "nyc_subway"
        assert "station_name" in parsed.variables.metadata
        assert parsed.max_lengths.get("station_name") == 22

    def test_all_simple_variables_have_descriptions(self, manifest_data):
        for var, meta in manifest_data["variables"]["simple"].items():
            assert meta.get("description"), f"{var} missing description"

    def test_arrays_declare_arrivals(self, manifest_data):
        assert "arrivals" in manifest_data["variables"]["arrays"]


# --------------------------------------------------------------------- #
# Station resolution
# --------------------------------------------------------------------- #
class TestStationResolution:
    def test_resolve_by_name(self):
        station, error = stations_module.resolve_station("Bedford Av")
        assert error is None
        assert station["complex_id"]
        assert station["feeds"]

    def test_resolve_by_gtfs_stop_id(self):
        station, error = stations_module.resolve_station(
            stations_module.resolve_station("Bedford Av")[0]["stops"][0]["stop_id"]
        )
        assert error is None
        assert station["name"] == "Bedford Av"

    def test_resolve_empty_query(self):
        station, error = stations_module.resolve_station("")
        assert station is None
        assert error

    def test_resolve_ambiguous_name_lists_options(self):
        station, error = stations_module.resolve_station("86 St")
        assert station is None
        assert "86 St (" in error

    def test_resolve_route_qualified_name(self):
        station, error = stations_module.resolve_station("86 St (1)")
        assert error is None
        assert "1" in station["routes"]

    def test_resolve_typo_suggests_close_match(self):
        station, error = stations_module.resolve_station("Bedfrd Av")
        assert station is None
        assert "Did you mean" in error

    def test_resolve_unknown_no_close_match(self):
        station, error = stations_module.resolve_station("Zzzqqq Xyzzy")
        assert station is None
        assert error

    def test_feeds_and_direction_labels_helpers(self):
        station, _ = stations_module.resolve_station("Bedford Av")
        assert stations_module.feeds_for(station) == station["feeds"]
        labels = stations_module.direction_labels(station)
        assert all({"N", "S"} <= set(v) for v in labels.values())


# --------------------------------------------------------------------- #
# Reroutes: trains from feeds the station isn't scheduled on
# --------------------------------------------------------------------- #
# 46 St (Queens) is scheduled M/R, so its feeds are bdfm + nqrw. When E trains
# run local in Queens they stop here too, but stay in the ace feed. Scoping the
# fetch to a station's scheduled feeds silently dropped them.
REROUTE_STATION = "46 St (M R)"
REROUTE_STOP = "G18"


class TestRerouteVisibility:
    def test_all_feeds_are_fetched(self, plugin, monkeypatch):
        requested = _patch_feeds_by_slug(monkeypatch, {})
        plugin.config = {"station": REROUTE_STATION, "show_alerts": False}
        plugin.fetch_data()
        assert set(requested) == set(plugin_module.ALL_FEEDS)

    def test_rerouted_route_from_another_feed_is_shown(self, plugin, monkeypatch):
        """An E at 46 St lives in the ace feed, which the station never scheduled."""
        _patch_feeds_by_slug(monkeypatch, {
            "gtfs-ace": _build_feed([("E", REROUTE_STOP + "N", 120)]),
            "gtfs-bdfm": _build_feed([("F", REROUTE_STOP + "N", 360)]),
        })
        plugin.config = {"station": REROUTE_STATION, "show_alerts": False}
        result = plugin.fetch_data()

        routes = {a["route"] for a in result.data["arrivals"]}
        assert routes == {"E", "F"}

    def test_rerouted_route_gets_a_status(self, plugin, monkeypatch):
        """A rerouted route is real service — it belongs in line_statuses."""
        _patch_feeds_by_slug(monkeypatch, {
            "gtfs-ace": _build_feed([("E", REROUTE_STOP + "N", 120)]),
        })
        plugin.config = {"station": REROUTE_STATION, "show_alerts": False}
        result = plugin.fetch_data()

        assert "E" in {ls["route"] for ls in result.data["line_statuses"]}
        assert all(a["status"] for a in result.data["arrivals"])

    def test_route_filter_still_applies_across_feeds(self, plugin, monkeypatch):
        _patch_feeds_by_slug(monkeypatch, {
            "gtfs-ace": _build_feed([("E", REROUTE_STOP + "N", 120)]),
            "gtfs-bdfm": _build_feed([("F", REROUTE_STOP + "N", 360)]),
        })
        plugin.config = {
            "station": REROUTE_STATION, "routes": "E", "show_alerts": False,
        }
        result = plugin.fetch_data()
        assert {a["route"] for a in result.data["arrivals"]} == {"E"}

    def test_same_trip_in_two_feeds_counted_once(self, plugin, station_stop,
                                                 monkeypatch):
        """A trip carried by two feeds is one train, not two."""
        _, stop_id = station_stop
        payload = _build_feed([("L", stop_id + "N", 120)])
        _patch_feeds_by_slug(monkeypatch, {"gtfs-l": payload, "gtfs-g": payload})
        plugin.config = {"station": TEST_STATION, "show_alerts": False}
        result = plugin.fetch_data()
        assert result.data["arrival_count"] == 1
