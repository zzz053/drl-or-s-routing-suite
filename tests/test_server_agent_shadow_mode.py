import sys
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server_agent import ServerAgent


def test_shadow_mode_installs_fallback_but_records_drl_advice():
    agent = ServerAgent.__new__(ServerAgent)
    fallback = {"status": "ok", "path": ["h1", 1, 2, "h2"], "path_source": "dijkstra"}
    drl = {"status": "ok", "path": ["h1", 1, 3, 2, "h2"], "decision_source": "drl_model", "model_used": True}

    result = agent._choose_final_path_response({}, drl, fallback, "shadow")

    assert result["path"] == ["h1", 1, 2, "h2"]
    assert result["path_source"] == "shadow_fallback"
    assert result["drl_shadow"]["path"] == ["h1", 1, 3, 2, "h2"]
    assert result["drl_shadow"]["model_used"] is True


def test_handle_path_request_retries_when_stale_link_down_blocks_only_path():
    graph = nx.DiGraph()
    graph.add_edge("10.0.0.28", 28)
    graph.add_edge(28, 1)
    graph.add_edge(1, 128985343745)
    graph.add_edge(128985343745, 128986965761)
    graph.add_edge(128986965761, "192.168.103.3")

    agent = ServerAgent.__new__(ServerAgent)
    agent.G = graph
    agent.route_mode = "spf"
    agent.link_down_set = {(1, 128985343745): 1, (128985343745, 1): 1}
    agent.host = {}

    response = agent.handle_path_request({
        "src": "10.0.0.28",
        "dst": "192.168.103.3",
        "route_mode": "spf",
    })

    assert response["status"] == "ok"
    assert response["path"] == [
        "10.0.0.28", 28, 1, 128985343745, 128986965761, "192.168.103.3"
    ]
    assert response["fallback_reason"] == "stale_link_down_ignored"
