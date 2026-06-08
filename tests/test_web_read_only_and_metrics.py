import sys
from pathlib import Path

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeWebState:
    def get_graph_snapshot(self, include_flows=False):
        return {"nodes": [], "edges": [], "versions": {}}

    def get_switch_flows(self, switch_id):
        return {"switch_id": switch_id, "flows": [], "flow_count": 0}


class FakeAgent:
    def __init__(self, web_mode="read_only"):
        import networkx as nx

        self.web_mode = web_mode
        self.clients = {}
        self.controller_to_switches = {}
        self.topo = {}
        self.host = {}
        self.G = nx.DiGraph()
        self.web_state = FakeWebState()
        self.link_down_set = {}
        self.path_service_sock = None
        self.path_service_host = "127.0.0.1"
        self.path_service_port = 8889
        self.flow_added = False
        self.path_requested = False

    def _get_web_ui_html(self):
        return "<html></html>"

    def handle_path_request(self, payload):
        self.path_requested = True
        return {"status": "ok", "path": [payload["src"], payload["dst"]]}

    def add_manual_flow(self, payload):
        self.flow_added = True
        return {"status": "ok", "flow": payload}

    def delete_manual_flow(self, switch_id, flow_id):
        return {"status": "ok", "flow_id": flow_id}


def make_client(agent):
    from web_api import register_web_api_routes

    app = Flask(__name__)
    register_web_api_routes(app, lambda: agent)
    return app.test_client()


def test_web_write_apis_are_rejected_in_read_only_mode():
    agent = FakeAgent(web_mode="read_only")
    client = make_client(agent)

    for method, url, payload in [
        ("post", "/api/path", {"src": "10.0.0.1", "dst": "10.0.0.2"}),
        ("post", "/api/flows", {"switch_id": 1, "out_port": 2, "match": {}}),
        ("delete", "/api/flows", {"switch_id": 1, "flow_id": 7}),
    ]:
        response = getattr(client, method)(url, json=payload)
        assert response.status_code == 403
        assert response.get_json()["error_code"] == "web_write_disabled"

    assert agent.path_requested is False
    assert agent.flow_added is False


def test_web_write_apis_still_work_in_development_mode():
    agent = FakeAgent(web_mode="development")
    client = make_client(agent)

    response = client.post("/api/path", json={"src": "10.0.0.1", "dst": "10.0.0.2"})

    assert response.status_code == 200
    assert agent.path_requested is True


def test_statistics_api_exposes_real_network_metric_summary():
    agent = FakeAgent()
    agent.G.add_edge(1, 2, edge_type="switch_link", throughput_mbps=10, utilization_percent=25, error_rate=0)
    agent.G.add_edge(2, 3, edge_type="switch_link", throughput_mbps=30, utilization_percent=75, error_rate=0.1)
    client = make_client(agent)

    response = client.get("/api/statistics")
    data = response.get_json()

    assert response.status_code == 200
    assert data["network"]["throughput_mbps"] == 40.0
    assert data["network"]["avg_utilization_percent"] == 50.0
    assert data["network"]["max_utilization_percent"] == 75.0
    assert data["network"]["active_link_count"] == 2
    assert data["network"]["error_link_count"] == 1


def test_web_ui_displays_real_metrics_without_simulation():
    text = (ROOT / "web_ui_html.py").read_text(encoding="utf-8")

    assert "Network Throughput" in text
    assert "Avg Utilization" in text
    assert "stats.network" in text
    statistics_body = text[text.index("async function updateStatistics"):text.index("// 测试API连接")]
    assert "Math.random()" not in statistics_body
    assert "* 100" not in statistics_body
    assert 'onclick="showAddFlowModal()"' not in text
    assert 'class="flow-delete" data-switch-id' not in text
