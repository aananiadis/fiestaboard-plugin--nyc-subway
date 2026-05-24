"""FiestaBoard plugin shim — re-exports from the inner package layout.

When FiestaBoard mounts the whole repo as ``plugins/<id>``, this file becomes
the plugin package. The real code lives in ``.plugins.nyc_subway`` (a nested
package); this shim re-exports the plugin class and aliases the submodules so
both FB and our own tests can import ``plugins.nyc_subway.X`` uniformly.
"""
import sys as _sys

from .plugins.nyc_subway import (
    gtfs_realtime_NYCT_pb2,
    gtfs_realtime_pb2,
    plugin,
    stations,
)
from .plugins.nyc_subway.plugin import NycSubwayPlugin

for _name, _mod in (
    ("plugin", plugin),
    ("gtfs_realtime_pb2", gtfs_realtime_pb2),
    ("gtfs_realtime_NYCT_pb2", gtfs_realtime_NYCT_pb2),
    ("stations", stations),
):
    _sys.modules[f"{__name__}.{_name}"] = _mod

__all__ = ["NycSubwayPlugin"]
