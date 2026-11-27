import urllib.request

import pytest


def _no_network(*args, **kwargs):
    # RuntimeError is not caught by fetch_rates, so an unmocked call fails
    # loudly instead of silently using the fallback.
    raise RuntimeError("Real network call in tests; mock urlopen instead")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
