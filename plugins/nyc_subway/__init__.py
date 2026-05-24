"""NYC Subway Arrivals plugin for FiestaBoard.

Thin re-export so FiestaBoard's loader can find ``NycSubwayPlugin`` on the
package, while the real implementation lives in :mod:`plugin`.
"""

from .plugin import NycSubwayPlugin

__all__ = ["NycSubwayPlugin"]
