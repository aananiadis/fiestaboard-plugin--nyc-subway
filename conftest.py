"""Test bootstrap: make the FiestaBoard host package importable.

The plugin imports `src.plugins.base` from its host repo. For local test runs,
point at your FiestaBoard checkout in one of two ways:

  1. Set the FIESTABOARD_PATH environment variable, or
  2. Create `.fiestaboard_path.local` in the repo root with the path on one line.

Both are gitignored / per-machine — no shared path is committed.
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent


def _resolve_host_path() -> Path | None:
    env = os.environ.get("FIESTABOARD_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    marker = _REPO_ROOT / ".fiestaboard_path.local"
    if marker.exists():
        text = marker.read_text().strip()
        if text:
            return Path(text).expanduser()
    return None


_host = _resolve_host_path()
if _host and (_host / "src" / "plugins" / "base.py").exists():
    sys.path.insert(0, str(_host))

# Plugin package itself must also be importable as a top-level path.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
