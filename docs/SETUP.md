# NYC Subway Arrivals Setup Guide

The NYC Subway Arrivals plugin shows upcoming train arrivals for a station you
choose, using the MTA's official realtime subway feeds.

## Quick Setup

### 1. Enable the Plugin

In the FiestaBoard web UI:
1. Go to **Integrations**
2. Find **NYC Subway Arrivals** and toggle it **On**

### 2. Configure NYC Subway Arrivals

1. Click the **Configure** button
2. Enter your **Station** (see "Finding your station" below)
3. Choose a **Direction**: Both, Uptown, or Downtown
4. Optionally set a **Route Filter** and **Arrivals Per Line**
5. Click **Save Changes**

### 3. Create a Board Template

1. Go to **Pages** in the web UI
2. Click **Create Page** or edit an existing page
3. Add plugin variables using the variable picker or type them directly

Example template:

```
{{nyc_subway.station_name}}
{{nyc_subway.formatted}}
```

### 4. View on Your Board

Once configured, the plugin output displays on your board when the page is active.

## Finding your station

Enter the station name as it appears on MTA maps, for example:

- `Times Sq-42 St`
- `Bedford Av`
- `Atlantic Av-Barclays Ctr`

Some station names are shared by several **unconnected** stations (there are
multiple `86 St` stations on different lines). If the name is ambiguous, the
plugin lists the options — qualify the name with its routes, e.g.:

```
86 St (1)
86 St (Q)
```

You can also enter a raw **GTFS stop id** (e.g. `127` for Times Sq-42 St) if
you know it.

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{nyc_subway.station_name}}` | Resolved station name | `Times Sq-42 St` |
| `{{nyc_subway.formatted}}` | Next train, one-line summary | `1 Uptown: 3 min` |
| `{{nyc_subway.arrival_count}}` | Total upcoming arrivals returned | `9` |
| `{{nyc_subway.updated_at}}` | Local time of last refresh | `14:05` |
| `{{nyc_subway.arrivals.0.route}}` | Route of the soonest train | `1` |
| `{{nyc_subway.arrivals.0.direction}}` | `uptown` or `downtown` | `uptown` |
| `{{nyc_subway.arrivals.0.eta}}` | Minutes until arrival | `3` |
| `{{nyc_subway.arrivals.0.label}}` | Platform label | `Uptown` |
| `{{nyc_subway.arrivals.0.terminus}}` | Terminal station | `Jamaica-179 St` |

## Configuration Reference

| Setting | Type | Required | Default | Description |
|---------|------|----------|---------|-------------|
| `enabled` | boolean | No | false | Enable/disable the plugin |
| `station` | string | Yes | — | Station name or GTFS stop id |
| `direction` | string | No | both | `both`, `uptown`, or `downtown` |
| `routes` | string | No | *(all)* | Comma-separated route filter |
| `max_arrivals` | integer | No | 3 | Trains per route and direction (1–6) |
| `refresh_seconds` | integer | No | 60 | Refresh interval, minimum 30 |

### Environment Variables

You can also configure the plugin via environment variables:

```bash
NYC_SUBWAY_STATION=Times Sq-42 St
NYC_SUBWAY_DIRECTION=both
```

## Troubleshooting

**Issue: Plugin shows "Unknown station"**
- Check the spelling; the plugin suggests close matches when it can.
- If the name is shared by several stations, qualify it with routes, e.g. `86 St (1)`.

**Issue: "Multiple stations named ..."**
- Use one of the route-qualified options listed in the message.

**Issue: Plugin shows "NO TRAINS"**
- There may genuinely be no upcoming trains (late nights, service changes).
- If you set a **Route Filter** or **Direction**, loosen it to confirm.

**Issue: Data not updating**
- Check the refresh interval setting.
- Verify the board can reach `api-endpoint.mta.info`.
- Check the Docker logs for error messages: `docker-compose logs -f`

## Notes on the data

The MTA splits subway realtime data across 8 GTFS-realtime feed endpoints by
line group. This plugin determines which feed(s) serve your station and
fetches only those. The feeds use the standard GTFS-realtime format plus the
MTA's NYCT extensions; the protobuf definitions are vendored in `proto/` and
compiled into the bindings committed in this repo.
