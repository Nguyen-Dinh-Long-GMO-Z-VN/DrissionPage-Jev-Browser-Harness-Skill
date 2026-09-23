import pytest

from jev_ultrafast import agent


@pytest.fixture(autouse=True)
def no_network_warmup(monkeypatch):
    """Tests stay offline: the connection warm-up would open real sockets."""
    monkeypatch.setattr(agent, "warm_connections", lambda: None)
