import socket

import pytest


def test_web_server_reports_occupied_bind_port(monkeypatch):
    import server_agent

    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("0.0.0.0", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    monkeypatch.setattr(server_agent, "WEB_PORT", port)

    agent = server_agent.ServerAgent.__new__(server_agent.ServerAgent)
    agent.web_http_server = None

    try:
        with pytest.raises(RuntimeError, match=f"Web port {port} is already in use"):
            agent.start_web_server()
    finally:
        occupied.close()


def test_stopping_server_closes_bound_web_http_server(monkeypatch):
    import server_agent

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("0.0.0.0", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setattr(server_agent, "WEB_PORT", port)

    agent = server_agent.ServerAgent.__new__(server_agent.ServerAgent)
    agent.web_http_server = None
    agent.is_running = False
    agent.clients = {}
    agent.sock = None
    agent.path_service_sock = None

    agent.start_web_server()
    assert agent.web_http_server is not None

    agent.stop()
    assert agent.web_http_server is None

    rebound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        rebound.bind(("0.0.0.0", port))
    finally:
        rebound.close()
