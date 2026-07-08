# NYC Subway Arrivals Plugin

Live upcoming train arrivals for a chosen NYC subway station, like the
countdown clocks in the station itself.

**→ [Setup Guide](./docs/SETUP.md)** - Configuration and setup instructions

## Overview

This plugin shows the next trains arriving at a subway station you pick,
grouped by route and direction, using the MTA's GTFS-realtime subway feeds.

## Template Variables

### Station

```
{{nyc_subway.station_name}}   # Resolved station name (e.g. "Times Sq-42 St")
```

### Arrivals

```
{{nyc_subway.formatted}}        # Next train as a one-line summary (e.g. "F to Jamaica-179 St: 3m")
{{nyc_subway.arrival_count}}    # Total number of upcoming arrivals returned
{{nyc_subway.updated_at}}       # Local time of the last refresh (e.g. "14:05")
```

### Arrivals Array

Each item is one upcoming train, soonest first:

```
{{nyc_subway.arrivals.0.route}}            # Route (e.g. "1", "Q", "GS")
{{nyc_subway.arrivals.0.direction}}        # "uptown" or "downtown"
{{nyc_subway.arrivals.0.direction_short}}  # "up" or "down" (for narrow boards)
{{nyc_subway.arrivals.0.eta}}              # Minutes until arrival
{{nyc_subway.arrivals.0.label}}            # Platform label (e.g. "Manhattan", "Forest Hills"),
                                           #   falling back to the terminus if MTA's label
                                           #   is generic ("Uptown", "Outbound", …)
{{nyc_subway.arrivals.0.terminus}}         # Terminal station, as on platform signs
                                           #   (e.g. "Jamaica-179 St")
{{nyc_subway.arrivals.0.color}}            # Vestaboard tile color matching the
                                           #   route's line bullet (e.g. "orange"
                                           #   for F, "yellow" for R). J/Z (brown)
                                           #   and L/S (gray) fall back to "white"
                                           #   since the board has no brown or
                                           #   gray tile.
{{nyc_subway.arrivals.0.status}}           # Line status: "green", "yellow", or "red"
```

### Line Status

Reads MTA service alerts and maps each line to a traffic-light color:

- **green** — no active alerts
- **yellow** — detour, modified service, stop moved, or other advisory
- **red** — significant delays, reduced service, or no service

```
{{nyc_subway.line_status}}              # Worst status across the station's lines
{{nyc_subway.line_statuses.0.route}}    # Per-line status array
{{nyc_subway.line_statuses.0.status}}
```

Use the `status` color rule in your template to tint arrivals when a line
is delayed. Set `show_alerts: false` to skip the alerts fetch entirely.

## Example Templates

### Simple Display

```
{center}{{nyc_subway.station_name}}
{{nyc_subway.formatted}}
```

### Detailed Display

```
{{nyc_subway.station_name}}
{{nyc_subway.arrivals.0.route}} {{nyc_subway.arrivals.0.terminus}} {{nyc_subway.arrivals.0.eta}}
{{nyc_subway.arrivals.1.route}} {{nyc_subway.arrivals.1.terminus}} {{nyc_subway.arrivals.1.eta}}
{{nyc_subway.arrivals.2.route}} {{nyc_subway.arrivals.2.terminus}} {{nyc_subway.arrivals.2.eta}}
{center}Updated {{nyc_subway.updated_at}}
```

## Configuration

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| enabled | boolean | false | Enable/disable the plugin |
| station | string | Times Sq-42 St | Station label (picked from the dropdown) or a GTFS stop id |
| direction | string | both | Which direction(s) to show: `both`, `uptown`/`up`, `downtown`/`down` |
| routes | string | *(all)* | Optional comma-separated line filter (e.g. "1,2,3") |
| max_arrivals | integer | 3 | Upcoming trains to list per line and direction |
| refresh_seconds | integer | 60 | How often to fetch new data (30–600 seconds) |
| show_alerts | boolean | true | Fetch MTA service alerts to color each line green/yellow/red |

If a station name is shared by several unconnected stations, qualify it with
its routes, e.g. `86 St (1)`. See the [Setup Guide](./docs/SETUP.md) for
details.

## Features

- Every line supported: numbered lines, lettered lines, the L, the Staten
  Island Railway, and the shuttles.
- Filter by direction (uptown/downtown/both) or by route.
- No API key required — the MTA feeds are public.

## Running tests locally

The plugin imports `src.plugins.base` from the FiestaBoard host repo. Point
the test bootstrap at your local FiestaBoard checkout by either:

- setting `FIESTABOARD_PATH=/path/to/FiestaBoard`, or
- writing that path to `.fiestaboard_path.local` in this repo root.

Both are per-machine (the file is gitignored). Then run `pytest`.

## License

Released under the [MIT License](./LICENSE.txt).

## Disclaimer

Subway arrival data is provided by the Metropolitan Transportation Authority
(MTA). This plugin is not affiliated with, endorsed by, or sponsored by the
MTA or New York City Transit.

Arrival times are estimates from the live feed and may be inaccurate,
delayed, or unavailable.
