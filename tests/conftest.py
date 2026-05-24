"""Plugin test fixtures for the NYC Subway Arrivals plugin."""

import pytest


@pytest.fixture(autouse=True)
def reset_plugin_singletons():
    """Reset plugin singletons before each test."""
    yield
