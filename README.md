# NYC Subway Arrivals Plugin

Live upcoming train arrivals for a chosen NYC subway station, like the
countdown clocks in the station itself.

![NYC Subway Arrivals Display](./docs/board-display.png)

**→ [Setup Guide](./docs/SETUP.md)** - Configuration and setup instructions

## Overview

This plugin shows the next trains arriving at a subway station you pick,
grouped by route and direction. It reads the MTA's official GTFS-realtime
subway feeds, which are **public and require no API key**. The plugin resolves
your station to its realtime feed(s) and fetches only what it needs.

## Template Variables

### Station

```
{{nyc_subway.station_name}}   # Resolved station name (e.g. "Times Sq-42 St")
```

### Arrivals

```
{{nyc_subway.formatted}}        # Next train as a one-line summary (e.g. "1 Uptown: 3 min")
{{nyc_subway.arrival_count}}    # Total number of upcoming arrivals returned
{{nyc_subway.updated_at}}       # Local time of the last refresh (e.g. "14:05")
```

### Arrivals Array

Each item is one upcoming train, soonest first:

```
{{nyc_subway.arrivals.0.route}}      # Route (e.g. "1", "Q", "GS")
{{nyc_subway.arrivals.0.direction}}  # "N" (northbound) or "S" (southbound)
{{nyc_subway.arrivals.0.eta}}        # Minutes until arrival
{{nyc_subway.arrivals.0.label}}      # Friendly direction (e.g. "Uptown", "Downtown")
```

## Example Templates

### Simple Display

```
{center}{{nyc_subway.station_name}}
{{nyc_subway.formatted}}
```

### Detailed Display

```
{{nyc_subway.station_name}}
{{nyc_subway.arrivals.0.route}} {{nyc_subway.arrivals.0.label}} {{nyc_subway.arrivals.0.eta}}
{{nyc_subway.arrivals.1.route}} {{nyc_subway.arrivals.1.label}} {{nyc_subway.arrivals.1.eta}}
{{nyc_subway.arrivals.2.route}} {{nyc_subway.arrivals.2.label}} {{nyc_subway.arrivals.2.eta}}
{center}Updated {{nyc_subway.updated_at}}
```

## Configuration

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| enabled | boolean | false | Enable/disable the plugin |
| station | string | *(required)* | Station name (e.g. "Times Sq-42 St") or a GTFS stop id |
| direction | string | both | Which direction(s) to show: `both`, `north`, `south` |
| routes | string | *(all)* | Optional comma-separated route filter (e.g. "1,2,3") |
| max_arrivals | integer | 3 | Upcoming trains to list per route and direction |
| refresh_seconds | integer | 60 | How often to fetch new data (minimum 30) |

If a station name is shared by several unconnected stations, qualify it with
its routes, e.g. `86 St (1)`. See the [Setup Guide](./docs/SETUP.md) for
details.

## Features

- **Live arrivals**: Real-time train predictions straight from the MTA feeds.
- **Every line**: All 8 subway feed groups (numbered lines, lettered lines,
  the L, the Staten Island Railway, and the shuttles) are supported.
- **Direction filtering**: Show only uptown, only downtown, or both.
- **No API Key Required**: The MTA subway realtime feeds are public.

## Author

Alexander Ananiadis

## Publishing to the registry

To list this plugin in the FiestaBoard registry, open a pull request adding a
`nyc_subway` entry to `plugin-registry.json` in the main FiestaBoard
repository.
