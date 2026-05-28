from pathlib import Path

from tools.acceptance_web_status import build_acceptance_status


class FakeGraph:
    def nodes(self):
        return [1]

    def edges(self):
        return [(1, 2)]


class FakeAgent:
    def __init__(self):
        self.clients = {("127.0.0.1", 6654): object()}
        self.path_service_sock = object()
        self.G = FakeGraph()
        self.controller_route_sessions = {
            ("127.0.0.1", 6654): [
                {
                    "src_ip": "10.0.0.28",
                    "dst_ip": "192.168.103.3",
                    "path_source": "dijkstra",
                    "updated_at": 100,
                }
            ]
        }


def test_build_acceptance_status_ready_when_control_plane_and_recent_session_match(tmp_path):
    config_path = tmp_path / "hybrid_acceptance.json"
    config_path.write_text(
        '{"external_interface":"eno1","controllers":{"ports":[6654],"forbidden_ports":[6671]},'
        '"hybrid":{"external_link_ports":[{"dpid":1,"port":20}],"gateway_ip":"10.0.0.254",'
        '"gateway_mac":"02:00:00:00:fe:01","real_routes":["192.168.103.0/24"]},'
        '"validation":{"virtual_host_name":"h28","virtual_host_ip":"10.0.0.28",'
        '"real_host_ip":"192.168.103.3"}}',
        encoding="utf-8",
    )

    status = build_acceptance_status(FakeAgent(), config_path=config_path)

    assert status["status"] == "ready"
    assert status["controllers_expected"] == 1
    assert status["controllers_connected"] == 1
    assert status["drl_connected"] is True
    assert status["recent_route_session"]["src_ip"] == "10.0.0.28"


def test_web_api_registers_acceptance_status_route():
    text = Path("web_api.py").read_text(encoding="utf-8")

    assert "/api/acceptance/status" in text
    assert "build_acceptance_status" in text
